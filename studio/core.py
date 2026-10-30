"""Shared plumbing: paths, projects, status/event log, ffprobe and ffmpeg runners."""
import json
import os
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROJECTS = ROOT / "projects"
FONTS = ROOT / "fonts"
MUSIC = ROOT / "music"
CURRENT = PROJECTS / ".current"

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".mts", ".m2ts", ".webm", ".3gp"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".webp", ".tif", ".tiff"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".opus"}


# ---------------------------------------------------------------- json io

def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # unique tmp name: several threads/processes may write the same file (status.json)
    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------- projects

class Project:
    def __init__(self, name):
        self.name = name
        self.dir = PROJECTS / name
        if not (self.dir / "project.json").exists():
            raise SystemExit(f"Projekt '{name}' nie istnieje. Utwórz: vs new {name} --source <folder>")

    # paths
    @property
    def meta_path(self): return self.dir / "project.json"
    @property
    def media_path(self): return self.dir / "media.json"
    @property
    def edl_path(self): return self.dir / "edl.json"
    @property
    def status_path(self): return self.dir / "status.json"
    @property
    def events_path(self): return self.dir / "events.jsonl"
    @property
    def feedback_path(self): return self.dir / "feedback.json"
    @property
    def thumbs(self): return self.dir / "thumbs"
    @property
    def sheets(self): return self.dir / "sheets"
    @property
    def analysis(self): return self.dir / "analysis"
    @property
    def cache(self): return self.dir / "cache"
    @property
    def renders(self): return self.dir / "renders"

    @property
    def meta(self): return read_json(self.meta_path, {})

    def media(self):
        return read_json(self.media_path, {"clips": {}})

    def save_media(self, m):
        write_json(self.media_path, m)

    def edl(self):
        return read_json(self.edl_path)

    def resolve_audio(self, f):
        """Music paths may be absolute, relative to the project, or to ~/video-studio/music."""
        p = Path(os.path.expanduser(f))
        for cand in (p, self.dir / p, self.dir / "music" / p, MUSIC / p):
            if cand.exists():
                return cand.resolve()
        raise FileNotFoundError(f"Nie znaleziono pliku muzyki: {f}")


def current_project_name():
    if CURRENT.exists():
        return CURRENT.read_text().strip() or None
    return None


def get_project(name=None):
    name = name or current_project_name()
    if not name:
        raise SystemExit("Brak aktywnego projektu. Użyj: vs use <nazwa> albo vs new <nazwa> --source <folder>")
    return Project(name)


def list_projects():
    if not PROJECTS.exists():
        return []
    return sorted(p.name for p in PROJECTS.iterdir() if (p / "project.json").exists())


# ---------------------------------------------------------------- events + status

_ev_lock = threading.Lock()


def event(project, msg, kind="info", **extra):
    rec = {"t": time.time(), "kind": kind, "msg": msg, **extra}
    with _ev_lock:
        with open(project.events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if kind != "progress":
        print(f"[{kind}] {msg}", flush=True)


class Status:
    """Live task state written to status.json so the panel can follow along."""

    def __init__(self, project, task, total_steps=1):
        self.p = project
        self.lock = threading.Lock()
        self.s = {
            "state": "running", "task": task, "stage": "", "detail": "",
            "progress": 0.0, "steps_done": 0, "steps_total": max(1, total_steps),
            "active": {}, "started": time.time(), "updated": time.time(), "eta": None,
            "pid": os.getpid(),
        }
        self._last_write = 0
        self.flush(force=True)

    def flush(self, force=False):
        now = time.time()
        if not force and now - self._last_write < 0.4:
            return
        s = self.s
        s["updated"] = now
        partial = sum(a.get("frac", 0) for a in s["active"].values())
        prog = min(1.0, (s["steps_done"] + partial) / s["steps_total"])
        s["progress"] = round(prog, 4)
        el = now - s["started"]
        s["eta"] = round(el / prog - el) if prog > 0.02 and s["state"] == "running" else None
        write_json(self.p.status_path, s)
        self._last_write = now

    def stage(self, stage, detail="", total_steps=None):
        with self.lock:
            self.s["stage"], self.s["detail"] = stage, detail
            if total_steps is not None:
                self.s["steps_total"] = max(1, total_steps)
                self.s["steps_done"] = 0
                self.s["started"] = time.time()
            self.flush(force=True)

    def begin(self, key, label, item=None):
        with self.lock:
            self.s["active"][key] = {"label": label, "frac": 0.0, "item": item}
            self.flush(force=True)

    def frac(self, key, f):
        with self.lock:
            if key in self.s["active"]:
                self.s["active"][key]["frac"] = max(0.0, min(1.0, f))
            self.flush()

    def end(self, key):
        with self.lock:
            self.s["active"].pop(key, None)
            self.s["steps_done"] += 1
            self.flush(force=True)

    def finish(self, state="done", detail="", **extra):
        with self.lock:
            self.s.update(state=state, detail=detail, active={}, eta=None, **extra)
            if state == "done":
                self.s["steps_done"] = self.s["steps_total"]
            self.flush(force=True)


# ---------------------------------------------------------------- ffmpeg

def ffprobe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip())
    return json.loads(out.stdout)


def media_duration(path):
    return float(ffprobe(path)["format"].get("duration", 0))


class FFmpegError(RuntimeError):
    pass


def run_ffmpeg(args, duration=None, on_progress=None, log_path=None, loglevel="error"):
    """Run ffmpeg, reporting progress (0..1) from -progress output when duration is known."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", loglevel, "-progress", "pipe:1", "-nostats"] + [str(a) for a in args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    err_chunks = []
    t = threading.Thread(target=lambda: err_chunks.append(proc.stderr.read()), daemon=True)
    t.start()
    for line in proc.stdout:
        if on_progress and duration and line.startswith("out_time_us="):
            try:
                us = int(line.split("=", 1)[1])
                on_progress(us / 1e6 / duration)
            except ValueError:
                pass
    proc.wait()
    t.join()
    err = "".join(err_chunks)
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(" ".join(cmd) + "\n" + err + "\n")
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg zakończył się błędem ({proc.returncode}):\n{err[-3000:]}\nKomenda: {' '.join(cmd)[:1500]}")
    return err


_nvenc_ok = None


def nvenc_available():
    global _nvenc_ok
    if _nvenc_ok is None:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=s=256x256:d=0.1",
                            "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True)
        _nvenc_ok = r.returncode == 0
    return _nvenc_ok


def video_codec_args(mode):
    """Encoder settings shared by every intermediate so concat can stream-copy."""
    q = {"preview": ("p2", "27", "23"), "final": ("p7", "17", "16")}[mode]
    if nvenc_available():
        return ["-c:v", "h264_nvenc", "-preset", q[0], "-tune", "hq", "-rc", "vbr", "-cq", q[1], "-b:v", "0",
                "-profile:v", "high", "-pix_fmt", "yuv420p", "-g", "60", "-bf", "2", "-video_track_timescale", "30000"]
    return ["-c:v", "libx264", "-preset", "veryfast" if mode == "preview" else "slow", "-crf", q[2],
            "-pix_fmt", "yuv420p", "-g", "60", "-video_track_timescale", "30000"]


def fmt_time(s):
    s = max(0, float(s))
    m, sec = divmod(s, 60)
    h, m = divmod(int(m), 60)
    return f"{h}:{m:02d}:{sec:05.2f}" if h else f"{m}:{sec:05.2f}"
