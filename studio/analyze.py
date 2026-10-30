"""Per-clip shot analysis: scene cuts, sharpness, exposure, shake and motion -> suggested best moments."""
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

import cv2
import numpy as np

from .core import Status, event, fmt_time, read_json, write_json

SR = 4          # analysed frames per second
W, H = 320, 180
VERSION = 3


def _frames(path, duration):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(path),
           "-vf", f"fps={SR},scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,format=gray",
           "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = W * H
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        yield np.frombuffer(buf, np.uint8).reshape(H, W)
    proc.wait()


def analyze_clip(clip, on_progress=None):
    n_expected = max(1, int(clip["duration"] * SR))
    bright, sharp, motion, shift, cuts = [], [], [], [], []
    prev = prev_g = None
    win = cv2.createHanningWindow((W, H), cv2.CV_32F)
    for i, g in enumerate(_frames(clip["path"], clip["duration"])):
        f = g.astype(np.float32)
        bright.append(float(f.mean()))
        sharp.append(float(cv2.Laplacian(g, cv2.CV_64F).var()))
        if prev is not None:
            diff = float(np.abs(f - prev).mean())
            motion.append(diff)
            # phaseCorrelate applies the window in place, so hand it copies
            (dx, dy), resp = cv2.phaseCorrelate(prev.copy(), f.copy(), win)
            shift.append((dx, dy))
            h1 = cv2.calcHist([g], [0], None, [32], [0, 256]); h0 = cv2.calcHist([prev_g], [0], None, [32], [0, 256])
            if diff > 28 and cv2.compareHist(h0, h1, cv2.HISTCMP_CORREL) < 0.6:
                cuts.append(round(i / SR, 2))
        else:
            motion.append(0.0); shift.append((0.0, 0.0))
        prev, prev_g = f, g
        if on_progress and i % 20 == 0:
            on_progress(i / n_expected)
    n = len(bright)
    if n == 0:
        return {"version": VERSION, "error": "brak klatek"}

    sh = np.array(shift)
    # shake = high-frequency jitter of global translation (second difference)
    jit = np.zeros(n)
    if n > 2:
        d2 = np.abs(np.diff(sh, n=2, axis=0)).sum(axis=1)
        jit[1:-1] = d2
    secs = int(np.ceil(n / SR))

    def per_sec(a):
        a = np.asarray(a, float)
        return [float(a[i * SR:(i + 1) * SR].mean()) for i in range(secs)]

    b, s, m, j = map(per_sec, (bright, sharp, motion, jit))
    s_arr, b_arr, j_arr, m_arr = map(np.array, (s, b, j, m))
    sref = np.percentile(s_arr, 90) + 1e-6
    sharp_n = np.clip(s_arr / sref, 0, 1.2)
    expo = 1 - np.clip(np.abs(b_arr - 120) / 120, 0, 1)
    shake_n = np.clip(j_arr / 6.0, 0, 1)
    still = np.clip(1 - m_arr / 3.0, 0, 1)   # completely static frames are a bit boring
    score = 0.45 * sharp_n + 0.30 * expo - 0.45 * shake_n - 0.10 * still + 0.25
    score = np.clip(score, 0, 1)

    # best non-overlapping windows, never across a cut
    bounds = [0.0] + cuts + [clip["duration"]]
    best = []
    wl = 4 if clip["duration"] >= 6 else max(1, int(clip["duration"]))
    cand = []
    for a in range(0, max(1, secs - wl + 1)):
        if any(a < c < a + wl for c in cuts):
            continue
        cand.append((float(score[a:a + wl].mean()), a))
    for sc, a in sorted(cand, reverse=True):
        if all(abs(a - x["in"]) >= wl for x in best):
            best.append({"in": float(a), "out": float(min(a + wl, clip["duration"])), "score": round(sc, 2)})
        if len(best) >= 4:
            break

    flags = []
    if float(np.median(b_arr)) < 45: flags.append("ciemne")
    if float(np.median(shake_n)) > 0.7: flags.append("trzęsie się")
    if float(np.median(sharp_n)) < 0.25: flags.append("nieostre")
    if float(np.median(m_arr)) < 0.6: flags.append("statyczne")
    return {
        "version": VERSION, "seconds": secs,
        "brightness": [round(x, 1) for x in b], "sharpness": [round(x, 1) for x in s],
        "motion": [round(x, 2) for x in m], "shake": [round(float(x), 2) for x in shake_n],
        "score": [round(float(x), 2) for x in score],
        "cuts": cuts, "shots": [[bounds[i], bounds[i + 1]] for i in range(len(bounds) - 1)],
        "best": sorted(best, key=lambda x: x["in"]), "flags": flags,
        "quality": round(float(np.mean(score)), 2),
    }


def analyze(project, ids=None, force=False):
    media = project.media()
    clips = media["clips"]
    todo = []
    for cid, c in clips.items():
        if c["type"] != "video" or (ids and cid not in ids):
            continue
        ap = project.analysis / f"{cid}.json"
        if force or (read_json(ap, {}) or {}).get("version") != VERSION:
            todo.append(cid)
    st = Status(project, "analyze", total_steps=len(todo) or 1)
    st.stage("Analiza ujęć", f"{len(todo)} klipów: ostrość, ekspozycja, drgania, cięcia")
    event(project, f"Analiza {len(todo)} klipów")

    def work(cid):
        st.begin(cid, f"{cid} · {os.path.basename(clips[cid]['path'])}")
        try:
            res = analyze_clip(clips[cid], lambda f: st.frac(cid, f))
            write_json(project.analysis / f"{cid}.json", res)
            return cid, res
        finally:
            st.end(cid)

    with ThreadPoolExecutor(max(2, (os.cpu_count() or 4) // 3)) as ex:
        for fut in as_completed([ex.submit(work, cid) for cid in todo]):
            try:
                cid, res = fut.result()
            except Exception as e:
                event(project, f"Analiza nieudana: {e}", "warn")
                continue
            c = clips[cid]
            c["quality"] = res.get("quality")
            c["flags"] = res.get("flags", [])
            c["best"] = res.get("best", [])
    project.save_media(media)
    st.finish("done", f"Przeanalizowano {len(todo)} klipów")
    event(project, f"Analiza zakończona ({len(todo)} klipów)", "done")


def describe_best(c):
    return ", ".join(f"{fmt_time(b['in'])}-{fmt_time(b['out'])}({b['score']})" for b in c.get("best", []))
