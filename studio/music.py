"""Music analysis: tempo, beats, bars and energy curve, so cuts can land on the beat."""
from pathlib import Path

import numpy as np

from .core import AUDIO_EXT, MUSIC, Status, event, fmt_time, read_json, write_json


_BT = None


def _beat_this(y, sr):
    """Beats + downbeats with Beat This! (ISMIR 2024, CPJKU) — far more reliable bar starts than librosa."""
    global _BT
    from beat_this.inference import Audio2Beats
    import torch
    if _BT is None:
        _BT = Audio2Beats(checkpoint_path="final0", device="cuda" if torch.cuda.is_available() else "cpu", dbn=False)
    beats, downbeats = _BT(y, sr)
    return [float(b) for b in beats], [float(d) for d in downbeats]


def analyze_music(project, file, status=True, force=False):
    import librosa

    path = project.resolve_audio(file)
    out = project.dir / "music_analysis" / f"{Path(path).stem}.json"
    cached = read_json(out)
    if cached and not force and cached.get("mtime") == int(path.stat().st_mtime):
        return cached
    st = Status(project, "music", 1) if status else None
    if st:
        st.begin("m", f"Analiza muzyki: {path.name}")
    y, sr = librosa.load(str(path), sr=22050, mono=True)
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    tempo = float(np.atleast_1d(tempo)[0])
    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    times = librosa.frames_to_time(np.arange(len(rms)), sr=sr, hop_length=hop)
    dur = float(len(y) / sr)
    # energy per 0.5 s, normalised 0..1
    bins = np.arange(0, dur + 0.5, 0.5)
    energy = [float(rms[(times >= a) & (times < a + 0.5)].mean()) if np.any((times >= a) & (times < a + 0.5)) else 0.0
              for a in bins[:-1]]
    mx = max(energy) or 1
    energy = [round(e / mx, 3) for e in energy]
    # strongest onsets = good "hit" points for impactful cuts
    onset = librosa.onset.onset_strength(y=y, sr=sr)
    ot = librosa.times_like(onset, sr=sr)
    peaks = librosa.util.peak_pick(onset, pre_max=20, post_max=20, pre_avg=40, post_avg=40, delta=0.4, wait=40)
    hits = sorted(((float(onset[i]), float(ot[i])) for i in peaks), reverse=True)[:24]
    beats = [round(float(b), 3) for b in beats]
    bars, engine = beats[::4], "librosa"
    try:
        y44, sr44 = librosa.load(str(path), sr=None, mono=True)
        bt, dbt = _beat_this(y44, sr44)
        if len(bt) > 8 and len(dbt) > 2:
            beats, bars, engine = [round(b, 3) for b in bt], [round(d, 3) for d in dbt], "beat_this"
            tempo = 60.0 / float(np.median(np.diff(bt)))
    except Exception as e:  # not installed / no GPU memory -> keep librosa
        event(project, f"Beat This! niedostępny ({type(e).__name__}), używam librosa", "warn")
    res = {
        "file": str(path), "mtime": int(path.stat().st_mtime), "duration": round(dur, 2), "tempo": round(tempo, 1),
        "beats": beats, "bars": bars, "engine": engine, "hits": sorted(round(t, 2) for _, t in hits),
        "energy_05s": energy,
    }
    write_json(out, res)
    if st:
        st.end("m")
        st.finish("done", f"{path.name}: {tempo:.0f} BPM, {fmt_time(dur)}")
    event(project, f"Muzyka {path.name}: {tempo:.0f} BPM, {len(beats)} uderzeń, {fmt_time(dur)}", "done")
    return res


def energy_summary(res, step=10):
    """Coarse textual energy profile, e.g. for deciding where the calm intro / climax sits."""
    e = res["energy_05s"]
    per = step * 2
    rows = []
    for i in range(0, len(e), per):
        v = float(np.mean(e[i:i + per]))
        rows.append(f"{fmt_time(i / 2):>8}  {'█' * int(v * 30):<30} {v:.2f}")
    return "\n".join(rows)


def music_notes(project):
    return read_json(project.dir / "music" / "music.json", {})


def list_music(project):
    """Tracks available to the project (its music/ folder + the shared ~/video-studio/music), with analysis + notes."""
    notes = music_notes(project)
    used = {}
    for m in (project.edl() or {}).get("music", []):
        used[Path(m["file"]).name] = used.get(Path(m["file"]).name, 0) + 1
    out = []
    for scope, d in (("project", project.dir / "music"), ("shared", MUSIC)):
        if not d.exists():
            continue
        for f in sorted(d.iterdir()):
            if f.suffix.lower() not in AUDIO_EXT or f.name.endswith(".part"):
                continue
            an = read_json(project.dir / "music_analysis" / f"{f.stem}.json") or {}
            e = an.get("energy_05s", [])
            spark = [round(float(np.mean(e[i:i + 10])), 2) for i in range(0, len(e), 10)] if e else []
            out.append({"file": f.name, "scope": scope, "size": f.stat().st_size, "added": f.stat().st_mtime,
                        "duration": an.get("duration"), "tempo": an.get("tempo"), "energy": spark,
                        "analyzed": bool(an), "note": notes.get(f.name, ""), "used": used.get(f.name, 0)})
    return out
