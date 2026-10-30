"""Local web panel: live status, activity feed, media library, timeline, render player and feedback."""
import os
import time

from flask import Flask, abort, jsonify, request, send_file, send_from_directory

from .core import ROOT, Project, media_duration, current_project_name, list_projects, read_json, write_json
from .timeline import chapters, compute, settings_of, validate

app = Flask(__name__, static_folder=None)
PANEL = ROOT / "panel"


def _proj(name):
    try:
        return Project(name)
    except SystemExit:
        abort(404)


def _mtime(path):
    try:
        return os.stat(path).st_mtime
    except FileNotFoundError:
        return 0


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


@app.get("/")
def index():
    return send_from_directory(PANEL, "index.html")


@app.get("/api/projects")
def projects():
    return jsonify({"projects": list_projects(), "current": current_project_name()})


@app.get("/api/p/<name>/state")
def state(name):
    p = _proj(name)
    st = read_json(p.status_path, {}) or {}
    if st.get("state") == "running" and not _alive(st.get("pid")):
        st["state"] = "interrupted"
    since = int(request.args.get("since", 0))
    events, n = [], 0
    try:
        with open(p.events_path, encoding="utf-8") as f:
            lines = f.readlines()
        n = len(lines)
        start = since if 0 < since <= n else max(0, n - 300)
        import json
        events = [json.loads(l) for l in lines[start:] if l.strip()]
    except FileNotFoundError:
        pass
    return jsonify({
        "status": st, "events": events, "event_count": n, "now": time.time(),
        "versions": {"media": _mtime(p.media_path), "edl": _mtime(p.edl_path),
                     "renders": _mtime(p.renders / "index.json"), "feedback": _mtime(p.feedback_path),
                     "music": max(_mtime(p.dir / "music"), _mtime(p.dir / "music" / "music.json"), _mtime(MUSIC))},
    })


@app.get("/api/p/<name>/summary")
def summary(name):
    """'Where did we stop' card shown when the panel opens."""
    import json
    p = _proj(name)
    edl = p.edl() or {}
    out = {"title": edl.get("title"), "version": edl.get("version"), "items": len(edl.get("timeline", [])),
           "music": len(edl.get("music", []))}
    try:
        from .timeline import compute, chapters
        items, total = compute(edl)
        out["total"], out["chapters"] = total, len(chapters(items))
    except Exception:
        pass
    renders = read_json(p.renders / "index.json", []) or []
    if renders:
        out["last_render"] = renders[-1]
        finals = [r for r in renders if r.get("mode") == "final"]
        out["last_final"] = finals[-1] if finals else None
    fb = read_json(p.feedback_path, []) or []
    fb = fb.get("items", []) if isinstance(fb, dict) else fb
    out["open_feedback"] = sum(1 for f in fb if not f.get("resolved"))
    notes, last_t = [], 0
    try:
        with open(p.events_path, encoding="utf-8") as f:
            lines = f.readlines()
        for l in lines[-400:]:
            e = json.loads(l)
            last_t = max(last_t, e.get("t", 0))
            if e.get("kind") == "claude":
                notes.append(e)
    except FileNotFoundError:
        pass
    out["last_notes"] = notes[-3:]
    out["last_activity"] = last_t
    return jsonify(out)


@app.get("/api/p/<name>/media")
def media(name):
    p = _proj(name)
    m = p.media()
    m["chapters"] = p.meta.get("chapters", [])
    return jsonify(m)


@app.get("/api/p/<name>/edl")
def edl(name):
    p = _proj(name)
    e = p.edl()
    if not e:
        return jsonify({"edl": None})
    try:
        items, total = compute(e)
        errs, warns = validate(e, p.media())
    except Exception as ex:  # half-written EDL while Claude edits it
        return jsonify({"edl": e, "error": str(ex)})
    clean = [{k: v for k, v in it.items() if not k.startswith("_") or k in ("_tkind", "_tname", "_td")} for it in items]
    music = []
    cursor = 0.0
    xf = float(settings_of(e)["music_crossfade"])
    for i, m in enumerate(e.get("music", [])):
        mm = dict(m)
        an = read_json(p.dir / "music_analysis" / f"{os.path.splitext(os.path.basename(m['file']))[0]}.json") or {}
        dur = an.get("duration")
        if not dur:
            try:
                dur = media_duration(p.resolve_audio(m["file"]))
            except (FileNotFoundError, RuntimeError):
                dur = 0
        ln = float(m.get("out") or dur) - float(m.get("in", 0))
        at = float(m["at"]) if "at" in m else (cursor if i == 0 else cursor - xf)
        cursor = at + ln
        mm.update(at=at, len=ln, tempo=an.get("tempo"),
                  bars=[b - float(m.get("in", 0)) + at for b in an.get("bars", [])
                        if float(m.get("in", 0)) <= b <= float(m.get("in", 0)) + ln])
        music.append(mm)
    return jsonify({"edl": e, "items": clean, "total": total, "chapters": chapters(items),
                    "errors": errs, "warnings": warns, "music": music, "settings": settings_of(e)})


@app.get("/api/p/<name>/renders")
def renders(name):
    p = _proj(name)
    idx = [r for r in read_json(p.renders / "index.json", []) if (p.renders / r["file"]).exists()]
    return jsonify({"renders": list(reversed(idx))})


@app.get("/api/p/<name>/feedback")
def feedback_get(name):
    return jsonify({"feedback": read_json(_proj(name).feedback_path, [])})


@app.post("/api/p/<name>/feedback")
def feedback_post(name):
    p = _proj(name)
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        abort(400)
    fb = read_json(p.feedback_path, [])
    rec = {"id": (max((f["id"] for f in fb), default=0) + 1), "t": time.time(), "text": text[:4000], "resolved": False}
    for k in ("item", "clip", "time", "render"):
        if data.get(k) is not None:
            rec[k] = data[k]
    fb.append(rec)
    write_json(p.feedback_path, fb)
    from .core import event
    event(p, f"Uwaga od Ciebie: {text[:200]}", "user")
    return jsonify(rec)


@app.get("/files/<name>/<path:sub>")
def files(name, sub):
    p = _proj(name)
    return send_from_directory(p.dir, sub, conditional=True, max_age=0 if sub.endswith(".json") else 3600)


@app.get("/src/<name>/<cid>")
def source(name, cid):
    p = _proj(name)
    c = p.media()["clips"].get(cid)
    if not c:
        abort(404)
    return send_file(c["path"], conditional=True)


# ---------------------------------------------------------------- music library

import re
import threading
import urllib.parse
import urllib.request

from .core import AUDIO_EXT, MUSIC, event

_analysing = set()
_STREAMING = re.compile(r"(spotify\.com|youtube\.com|youtu\.be|music\.apple\.com|tidal\.com|deezer\.com|soundcloud\.com)", re.I)


def _safe_name(name):
    name = os.path.basename(name or "").strip()
    name = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", "_", name)
    return name[:150] or "utwor.mp3"


def _unique(d, name):
    base, ext = os.path.splitext(name)
    cand, i = name, 2
    while (d / cand).exists():
        cand = f"{base} ({i}){ext}"; i += 1
    return cand


def _analyse_async(p, fname):
    key = (p.name, fname)
    if key in _analysing:
        return
    _analysing.add(key)

    def run():
        from .music import analyze_music
        try:
            analyze_music(p, fname, status=False)
        except Exception as e:
            event(p, f"Nie udało się przeanalizować {fname}: {e}", "warn")
        finally:
            _analysing.discard(key)
            os.utime(p.dir / "music")  # bump version for the panel
    threading.Thread(target=run, daemon=True).start()


@app.get("/api/p/<name>/music")
def music_list(name):
    from .music import list_music
    p = _proj(name)
    tracks = list_music(p)
    for t in tracks:
        t["analysing"] = (p.name, t["file"]) in _analysing
        if not t["analyzed"] and not t["analysing"]:
            _analyse_async(p, t["file"])
            t["analysing"] = True
    return jsonify({"tracks": tracks})


@app.post("/api/p/<name>/music/upload")
def music_upload(name):
    p = _proj(name)
    d = p.dir / "music"
    d.mkdir(exist_ok=True)
    saved = []
    for f in request.files.getlist("files"):
        fname = _safe_name(f.filename)
        if os.path.splitext(fname)[1].lower() not in AUDIO_EXT:
            continue
        fname = _unique(d, fname)
        f.save(d / fname)
        saved.append(fname)
        _analyse_async(p, fname)
    if saved:
        event(p, f"Dodano muzykę: {', '.join(saved)}", "user")
    return jsonify({"saved": saved})


@app.post("/api/p/<name>/music/url")
def music_url(name):
    p = _proj(name)
    url = ((request.get_json(force=True) or {}).get("url") or "").strip()
    if not re.match(r"^https?://", url):
        return jsonify({"error": "To nie wygląda na link."}), 400
    if _STREAMING.search(url):
        return jsonify({"error": "Linki do serwisów streamingowych (Spotify, YouTube, Apple Music…) nie są obsługiwane — "
                                 "te utwory są chronione i nie wolno ich pobierać. Wgraj plik albo podaj bezpośredni link "
                                 "do pliku z darmowej biblioteki (Pixabay, Free Music Archive…)."}), 400
    d = p.dir / "music"
    d.mkdir(exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 video-studio"})
        with urllib.request.urlopen(req, timeout=30) as r:
            ctype = r.headers.get("Content-Type", "")
            fname = _safe_name(urllib.parse.unquote(os.path.basename(urllib.parse.urlparse(r.geturl()).path)))
            cd = r.headers.get("Content-Disposition", "")
            m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd)
            if m:
                fname = _safe_name(urllib.parse.unquote(m.group(1)))
            if fname == "utwor.mp3":
                fname = "utwor"
            ext = os.path.splitext(fname)[1].lower()
            if ctype.startswith(("text/", "application/json", "application/xhtml")):
                return jsonify({"error": f"Pod tym linkiem jest strona, nie plik audio ({ctype.split(';')[0]}). "
                                         "Użyj bezpośredniego linku „Pobierz”."}), 400
            if ext not in AUDIO_EXT:
                if not ctype.startswith("audio/"):
                    return jsonify({"error": f"Pod tym linkiem nie ma pliku audio ({ctype or 'nieznany typ'}). "
                                             "Użyj bezpośredniego linku „Pobierz”."}), 400
                fname = fname + {"audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/ogg": ".ogg",
                                 "audio/flac": ".flac", "audio/mp4": ".m4a"}.get(ctype.split(";")[0], ".mp3")
            fname = _unique(d, fname)
            tmp = d / (fname + ".part")
            size = 0
            with open(tmp, "wb") as out:
                while chunk := r.read(1 << 16):
                    size += len(chunk)
                    if size > 200 * 1024 * 1024:
                        out.close(); tmp.unlink(missing_ok=True)
                        return jsonify({"error": "Plik większy niż 200 MB"}), 400
                    out.write(chunk)
            os.replace(tmp, d / fname)
    except Exception as e:
        return jsonify({"error": f"Nie udało się pobrać: {e}"}), 400
    event(p, f"Pobrano muzykę z linku: {fname}", "user")
    _analyse_async(p, fname)
    return jsonify({"saved": [fname]})


@app.post("/api/p/<name>/music/note")
def music_note(name):
    p = _proj(name)
    data = request.get_json(force=True) or {}
    path = p.dir / "music" / "music.json"
    notes = read_json(path, {})
    notes[_safe_name(data.get("file"))] = (data.get("note") or "")[:500]
    write_json(path, notes)
    return jsonify({"ok": True})


@app.post("/api/p/<name>/music/delete")
def music_delete(name):
    p = _proj(name)
    fname = _safe_name((request.get_json(force=True) or {}).get("file"))
    f = p.dir / "music" / fname
    if not f.exists():
        abort(404)
    if any(os.path.basename(m["file"]) == fname for m in (p.edl() or {}).get("music", [])):
        return jsonify({"error": "Ten utwór jest użyty w montażu — poproś Claude o podmianę."}), 400
    trash = p.dir / "music" / ".usuniete"
    trash.mkdir(exist_ok=True)
    os.replace(f, trash / fname)
    event(p, f"Usunięto utwór {fname} (kopia w music/.usuniete)", "user")
    return jsonify({"ok": True})


@app.get("/music/<name>/<scope>/<path:fname>")
def music_file(name, scope, fname):
    p = _proj(name)
    d = p.dir / "music" if scope == "project" else MUSIC
    return send_from_directory(d, fname, conditional=True)


def serve(port=8765, host="127.0.0.1"):
    print(f"Panel: http://{host}:{port}")
    app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 ** 3
    app.run(host=host, port=port, threaded=True, debug=False)
