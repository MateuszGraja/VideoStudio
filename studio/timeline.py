"""EDL (edl.json) loading, defaults, timing computation and validation."""
from .core import fmt_time

DEFAULT_SETTINGS = {
    "width": 1920, "height": 1080, "fps": 30,
    "look": "none",              # none | warm | cool | punchy | film | bw
    "fit": "auto",               # auto | cover | contain | blur
    "clip_volume": 0.0,          # original camera audio level (0 = music only)
    "loudness": -14,             # LUFS target of the final mix (YouTube: -14)
    "transition_duration": 0.6,
    "accent": "#FFC440",
    "music_crossfade": 3.0,
    "end_fade": 2.5,             # fade video+audio to black at the very end
}

XFADE_NAMES = {"fade", "fadeblack", "fadewhite", "dissolve", "wipeleft", "wiperight", "wipeup", "wipedown",
               "slideleft", "slideright", "slideup", "slidedown", "circleopen", "circleclose", "smoothleft",
               "smoothright", "smoothup", "smoothdown", "radial", "zoomin", "distance", "hblur", "pixelize"}


def settings_of(edl):
    s = dict(DEFAULT_SETTINGS)
    s.update((edl or {}).get("settings", {}))
    return s


def transition_of(item):
    """-> (kind, xfade_name). kind: cut | fade (dip to black) | xfade."""
    t = (item.get("transition") or "cut").lower()
    if t in ("cut", "none"):
        return "cut", None
    if t in ("fade", "dip", "black"):
        return "fade", None
    if t == "xfade":
        return "xfade", "fade"
    if t.startswith("xfade:"):
        return "xfade", t.split(":", 1)[1]
    if t in XFADE_NAMES:
        return "xfade", t
    return "cut", None


def item_duration(it):
    if it["type"] == "clip":
        return (float(it["out"]) - float(it["in"])) / float(it.get("speed", 1) or 1)
    return float(it.get("duration", 4 if it["type"] == "title" else 3))


def compute(edl):
    """Annotate every item with idx, start, dur, end on the final timeline; return (items, total)."""
    s = settings_of(edl)
    fps = float(s["fps"])
    q = lambda x: round(max(1, round(x * fps)) / fps, 4)   # snap to whole frames
    items = []
    t = 0.0
    for i, raw in enumerate(edl.get("timeline", [])):
        it = dict(raw)
        it["idx"] = i
        it["dur"] = q(item_duration(it))
        kind, name = transition_of(it) if i > 0 else ("cut", None)
        it["_tkind"], it["_tname"] = kind, name
        it["_td"] = q(float(it.get("transition_duration", s["transition_duration"]))) if kind != "cut" else 0.0
        if kind == "xfade":
            t -= it["_td"]
        it["start"] = round(t, 4)
        t += it["dur"]
        it["end"] = round(t, 4)
        items.append(it)
    return items, round(t, 4)


def chapters(items):
    out = []
    for it in items:
        name = it.get("chapter_marker") or (it.get("text") if it["type"] == "title" and it.get("style") == "chapter" else None)
        if name:
            out.append((it["start"], name))
    if out and out[0][0] > 0.5:
        out.insert(0, (0.0, "Intro"))
    return out


def validate(edl, media):
    errs, warns = [], []
    if not edl:
        return ["Brak edl.json"], []
    clips = media.get("clips", {})
    items, total = compute(edl)
    for it in items:
        tag = f"#{it['idx']}"
        ty = it.get("type")
        if ty not in ("clip", "image", "title"):
            errs.append(f"{tag}: nieznany typ '{ty}'")
            continue
        if ty in ("clip", "image"):
            c = clips.get(it.get("id"))
            if not c:
                errs.append(f"{tag}: nie ma klipu '{it.get('id')}'")
                continue
            if c.get("missing"):
                errs.append(f"{tag}: plik {it['id']} zniknął z dysku ({c['path']})")
                continue
            if ty == "clip":
                if c["type"] != "video":
                    errs.append(f"{tag}: {it['id']} to zdjęcie — użyj type=image")
                    continue
                a, b = float(it.get("in", 0)), float(it.get("out", 0))
                if not (0 <= a < b):
                    errs.append(f"{tag}: zły zakres in={a} out={b}")
                if b > c["duration"] + 0.05:
                    errs.append(f"{tag}: out={b} > długość klipu {c['duration']}")
                if it.get("speed", 1) <= 0:
                    errs.append(f"{tag}: speed musi być > 0")
        if it["dur"] < 0.3:
            errs.append(f"{tag}: za krótki ({it['dur']:.2f}s)")
        if it["_tkind"] == "xfade":
            prev = items[it["idx"] - 1]
            if it["_td"] >= min(it["dur"], prev["dur"]) - 0.05:
                errs.append(f"{tag}: przejście {it['_td']}s dłuższe niż ujęcie")
            if it["_tname"] not in XFADE_NAMES:
                errs.append(f"{tag}: nieznane przejście xfade '{it['_tname']}'")
        if it["dur"] > 12 and ty == "clip":
            warns.append(f"{tag}: ujęcie {it['dur']:.1f}s — długie, rozważ skrócenie")
    if not edl.get("music"):
        warns.append("Brak muzyki w EDL")
    return errs, warns


def table(edl, media):
    items, total = compute(edl)
    clips = media.get("clips", {})
    rows = []
    for it in items:
        if it["type"] == "title":
            what = f"TITLE[{it.get('style', 'main')}] {it.get('text', '')!r}"
        else:
            c = clips.get(it.get("id"), {})
            rng = f" {fmt_time(it['in'])}-{fmt_time(it['out'])}" if it["type"] == "clip" else ""
            what = f"{it['type']} {it.get('id')}{rng}" + (f" x{it['speed']}" if it.get("speed", 1) != 1 else "")
            if c.get("desc"):
                what += f"  — {c['desc'][:50]}"
        cap = it.get("caption")
        if cap:
            what += f"  [napis: {cap if isinstance(cap, str) else cap.get('text')}]"
        tr = it["_tkind"] if it["_tkind"] != "xfade" else f"xfade:{it['_tname']}"
        rows.append(f"{it['idx']:>3}  {fmt_time(it['start']):>9}  {it['dur']:5.2f}s  {tr:<16} {what}")
    rows.append(f"RAZEM: {fmt_time(total)}  ({len(items)} elementów)")
    return "\n".join(rows)
