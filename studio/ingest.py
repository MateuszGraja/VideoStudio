"""Scan source folders: metadata, poster thumbnails and contact sheets (what Claude looks at)."""
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from .core import IMAGE_EXT, VIDEO_EXT, Status, event, ffprobe, fmt_time
from .graphics import font

_NAME_DATE = re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})[-_ T]?(\d{2})[-_:]?(\d{2})[-_:]?(\d{2})")


def _local(dt):
    return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def _date_for(path, probe=None):
    """Best guess of capture time: container tag > EXIF > filename > mtime."""
    if probe:
        tags = dict(probe.get("format", {}).get("tags", {}))
        for st in probe.get("streams", []):
            tags.update({k: v for k, v in st.get("tags", {}).items() if k not in tags})
        for k in ("com.apple.quicktime.creationdate", "creation_time"):
            v = tags.get(k)
            if v and not v.startswith("1970"):
                try:
                    return _local(datetime.fromisoformat(v.replace("Z", "+00:00"))), "metadata"
                except ValueError:
                    pass
    if path.suffix.lower() in IMAGE_EXT:
        try:
            exif = Image.open(path).getexif()
            v = exif.get_ifd(0x8769).get(36867) or exif.get(306)
            if v:
                return datetime.strptime(v, "%Y:%m:%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S"), "exif"
        except Exception:
            pass
    m = _NAME_DATE.search(path.name)
    if m:
        try:
            return datetime(*map(int, m.groups())).strftime("%Y-%m-%d %H:%M:%S"), "filename"
        except ValueError:
            pass
    return _local(datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)), "mtime"


_EVENT_PREFIX = re.compile(r"^\s*(?:\d{4}[_.-]\d{2}[_.-]\d{2}|\d{2}[_.-]\d{2})(?:\s*-\s*(?:\d{4}[_.-])?\d{2}[_.-]\d{2})?\s*-\s*")


def _place(path, sources):
    """Folder structure -> group (top folder, e.g. DEVELOPMENT/COMPETITIONS/TEAM) and event (its subfolder)."""
    for src in sources:
        try:
            rel = Path(path).relative_to(src)
        except ValueError:
            continue
        parts = rel.parts[:-1]
        group = parts[0] if parts else ""
        ev = parts[1] if len(parts) > 1 else ""
        label = _EVENT_PREFIX.sub("", ev).strip() or ev
        # 3rd level = day inside a trip, e.g. COMPETITIONS/09_10 - 09_22 SUAS/2026_09_15 - zawody dzien 1
        day = parts[2] if len(parts) > 2 else ""
        day_label = _EVENT_PREFIX.sub("", day).strip() if day else ""
        return {"group": group, "event": ev, "event_label": label, "day": day, "day_label": day_label,
                "subpath": "/".join(parts[2:])}
    return {"group": "", "event": "", "event_label": "", "day": "", "day_label": "", "subpath": ""}


_FOLDER_DATE = re.compile(r"^\s*(\d{4})[_.-](\d{2})[_.-](\d{2})")


def _folder_date(c):
    """A dated folder (day, else event) is the user's truth: camera clocks are often wrong (2005, UTC abroad)."""
    if c.get("date_source") == "folder":
        c["date"] = c.get("orig_date", c["date"])
        c["date_source"] = c.get("orig_date_source", "mtime")
    for f in (c.get("day"), c.get("event")):
        m = _FOLDER_DATE.match(f or "")
        if m:
            d = "-".join(m.groups())
            if not c["date"].startswith(d):
                c["orig_date"], c["orig_date_source"] = c["date"], c["date_source"]
                c["date"] = d + c["date"][10:]
                c["date_source"] = "folder"
            return


def _probe_video(path):
    pr = ffprobe(path)
    v = next((s for s in pr["streams"] if s["codec_type"] == "video"), None)
    if not v:
        return None
    a = next((s for s in pr["streams"] if s["codec_type"] == "audio"), None)
    w, h = int(v.get("width", 0)), int(v.get("height", 0))
    rot = 0
    for sd in v.get("side_data_list", []):
        if "rotation" in sd:
            rot = int(sd["rotation"])
    rot = int(v.get("tags", {}).get("rotate", rot))
    if abs(rot) % 180 == 90:
        w, h = h, w
    num, den = (v.get("avg_frame_rate") or v.get("r_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    date, date_src = _date_for(path, pr)
    return {
        "type": "video", "width": w, "height": h, "fps": round(fps, 3),
        "duration": round(float(pr["format"].get("duration") or v.get("duration") or 0), 3),
        "codec": v.get("codec_name"), "has_audio": a is not None,
        "hdr": v.get("color_transfer") in ("smpte2084", "arib-std-b67"),
        "pix_fmt": v.get("pix_fmt"), "date": date, "date_source": date_src,
    }


def _probe_image(path):
    im = ImageOps.exif_transpose(Image.open(path))
    date, date_src = _date_for(path)
    return {"type": "image", "width": im.width, "height": im.height, "duration": 0, "date": date, "date_source": date_src}


def _grab(path, t, width, out):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", str(path),
                    "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(out)], check=False)
    return out if out.exists() else None


def sheet_times(duration, n=None):
    if n is None:
        n = 8 if duration <= 20 else 12 if duration <= 90 else 16 if duration <= 300 else 24
    n = max(1, n)
    return [duration * (i + 0.5) / n for i in range(n)]


def contact_sheet(clip, path, out, start=0.0, end=None, n=None, cols=4, cell_w=400):
    """Grid of frames with timestamps — the main way Claude 'watches' a clip."""
    end = clip["duration"] if end is None else min(end, clip["duration"])
    span = max(0.05, end - start)
    times = [start + t for t in sheet_times(span, n)]
    with tempfile.TemporaryDirectory() as td:
        frames = [_grab(path, t, cell_w, Path(td) / f"{i:03d}.jpg") for i, t in enumerate(times)]
        imgs = [Image.open(f).convert("RGB") if f else Image.new("RGB", (cell_w, cell_w * 9 // 16)) for f in frames]
        cell_h = max(im.height for im in imgs)
        rows = (len(imgs) + cols - 1) // cols
        head = 44
        sheet = Image.new("RGB", (cols * cell_w, head + rows * cell_h), (18, 18, 20))
        d = ImageDraw.Draw(sheet)
        d.text((12, 10), f"{clip['id']}  ·  {clip['date']}  ·  {fmt_time(clip['duration'])}  ·  "
                         f"{clip['width']}x{clip['height']}  ·  {Path(path).name}  ·  zakres {fmt_time(start)}–{fmt_time(end)}",
               font=font("Inter", 20, "SemiBold"), fill=(235, 235, 235))
        f_ts = font("Inter", 18, "Bold")
        for i, (im, t) in enumerate(zip(imgs, times)):
            x, y = (i % cols) * cell_w, head + (i // cols) * cell_h
            sheet.paste(im, (x, y))
            label = fmt_time(t)
            tw = d.textlength(label, font=f_ts)
            d.rectangle([x + 6, y + 6, x + 18 + tw, y + 32], fill=(0, 0, 0))
            d.text((x + 12, y + 8), label, font=f_ts, fill=(255, 220, 90))
        out.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(out, quality=85)
    return out


def _process(project, cid, clip):
    path = Path(clip["path"])
    poster = project.thumbs / f"{cid}.jpg"
    if clip["type"] == "video":
        if not poster.exists():
            _grab(path, clip["duration"] * 0.4, 480, poster)
        sheet = project.sheets / f"{cid}.jpg"
        if not sheet.exists():
            contact_sheet(clip, path, sheet)
    else:
        if not poster.exists():
            im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
            im.thumbnail((480, 480))
            poster.parent.mkdir(parents=True, exist_ok=True)
            im.save(poster, quality=85)
    return cid


def ingest(project, extra_sources=()):
    meta = project.meta
    sources = list(dict.fromkeys(meta.get("sources", []) + [str(Path(s).expanduser().resolve()) for s in extra_sources]))
    if extra_sources:
        meta["sources"] = sources
        from .core import write_json
        write_json(project.meta_path, meta)

    media = project.media()
    clips = media["clips"]
    known = {c["path"]: cid for cid, c in clips.items()}
    files = []
    for src in sources:
        sp = Path(src)
        it = [sp] if sp.is_file() else sorted(p for p in sp.rglob("*") if p.is_file())
        for p in it:
            ext = p.suffix.lower()
            if ext in VIDEO_EXT or ext in IMAGE_EXT:
                files.append(p.resolve())

    # relink clips whose files were moved/renamed folders (keeps ids, descriptions and the edit)
    file_set = {str(p) for p in files}
    missing = {cid: c for cid, c in clips.items() if c["path"] not in file_set and not Path(c["path"]).exists()}
    relinked = 0
    if missing:
        cand = {}
        for p in files:
            if str(p) not in known:
                cand.setdefault(p.name, []).append(p)
        for cid, c in missing.items():
            name = Path(c["path"]).name
            opts = [p for p in cand.get(name, []) if not c.get("size") or p.stat().st_size == c["size"]]
            if len(opts) == 1:
                known.pop(c["path"], None)
                c["path"] = str(opts[0]); known[c["path"]] = cid
                cand[name].remove(opts[0])
                relinked += 1
    for cid, c in clips.items():
        if Path(c["path"]).exists():
            c.pop("missing", None)
            c.setdefault("size", Path(c["path"]).stat().st_size)
        else:
            c["missing"] = True
    if relinked:
        event(project, f"Podpięto {relinked} przeniesionych plików pod nowe ścieżki (opisy zachowane)")
    gone = sum(1 for c in clips.values() if c.get("missing"))
    if gone:
        event(project, f"{gone} klipów nie ma już na dysku (oznaczone jako brakujące)", "warn")

    new = [p for p in files if str(p) not in known]
    st = Status(project, "ingest", total_steps=len(new) * 2 or 1)
    event(project, f"Skanuję {len(sources)} folder(y): {len(files)} plików, nowych: {len(new)}")
    st.stage("Odczyt metadanych", f"{len(new)} nowych plików")

    probed = {}

    def probe(p):
        try:
            return p, (_probe_video(p) if p.suffix.lower() in VIDEO_EXT else _probe_image(p))
        except Exception as e:
            return p, {"error": str(e)}

    with ThreadPoolExecutor(8) as ex:
        for fut in as_completed([ex.submit(probe, p) for p in new]):
            p, info = fut.result()
            st.begin(str(p), p.name); st.end(str(p))
            if info and "error" not in info:
                probed[p] = info
            else:
                event(project, f"Pominięto {p.name}: {(info or {}).get('error', 'brak strumienia wideo')}", "warn")

    # stable ids in chronological order of everything known
    allc = list(clips.values()) + [dict(path=str(p), size=p.stat().st_size, **i) for p, i in probed.items()]
    allc.sort(key=lambda c: (c["date"], c["path"]))
    used = {int(cid[1:]) for cid in clips}
    nxt = max(used, default=0) + 1
    for c in allc:
        if c["path"] in known:
            continue
        cid = f"c{nxt:04d}"; nxt += 1
        c.update(id=cid, desc="", tags=[], chapter=None, rating=None)
        clips[cid] = c
    for c in clips.values():
        c.update(_place(c["path"], sources))
        _folder_date(c)
    media["clips"] = dict(sorted(clips.items(), key=lambda kv: (kv[1]["date"], kv[0])))
    project.save_media(media)

    project.thumbs.mkdir(exist_ok=True)
    project.sheets.mkdir(exist_ok=True)
    todo = [cid for cid, c in clips.items() if not (project.thumbs / f"{cid}.jpg").exists()
            or (c["type"] == "video" and not (project.sheets / f"{cid}.jpg").exists())]
    st.stage("Miniatury i arkusze klatek", f"{len(todo)} klipów", total_steps=len(todo) or 1)
    with ThreadPoolExecutor(max(2, (os.cpu_count() or 4) // 2)) as ex:
        futs = {}
        for cid in todo:
            st.begin(cid, Path(clips[cid]["path"]).name)
            futs[ex.submit(_process, project, cid, clips[cid])] = cid
        for fut in as_completed(futs):
            cid = futs[fut]
            try:
                fut.result()
            except Exception as e:
                event(project, f"Błąd miniatur {cid}: {e}", "warn")
            st.end(cid)

    vids = [c for c in clips.values() if c["type"] == "video"]
    total = sum(c["duration"] for c in vids)
    dates = sorted(c["date"] for c in clips.values())
    groups = {}
    for c in clips.values():
        groups[c.get("group") or "(główny folder)"] = groups.get(c.get("group") or "(główny folder)", 0) + 1
    summary = (f"Biblioteka: {len(vids)} wideo ({fmt_time(total)}), {len(clips) - len(vids)} zdjęć"
               + (f", od {dates[0][:10]} do {dates[-1][:10]}" if dates else "")
               + " · " + ", ".join(f"{g}: {n}" for g, n in sorted(groups.items())))
    st.finish("done", summary)
    event(project, summary, "done")
    return media
