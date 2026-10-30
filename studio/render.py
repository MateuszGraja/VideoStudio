"""Render an EDL: per-item segments (cached) -> crossfade blocks -> concat -> music mix + loudness -> MP4."""
import hashlib
import json
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

from .core import (FFmpegError, Status, event, fmt_time, media_duration, nvenc_available, read_json,
                   run_ffmpeg, video_codec_args, write_json)
from .graphics import caption_png, person_png, photo_layers, title_png
from .timeline import chapters, compute, settings_of, validate

VERSION = 3
LOOKS = {
    "none": "",
    "warm": "colorbalance=rs=0.04:bs=-0.04:rm=0.03:bm=-0.03,eq=saturation=1.08:contrast=1.04",
    "cool": "colorbalance=rs=-0.03:bs=0.04:bm=0.02,eq=saturation=1.02:contrast=1.03",
    "punchy": "eq=contrast=1.08:saturation=1.22,unsharp=5:5:0.4",
    "film": "eq=contrast=1.06:saturation=0.92,colorbalance=rs=0.03:bs=-0.03:rh=-0.02:bh=0.03,vignette=PI/5",
    "bw": "hue=s=0,eq=contrast=1.12",
}
HDR_TONEMAP = ("zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
               "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")


def _hex(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (255,)


def _atempo(speed):
    parts, s = [], speed
    while s > 2.0:
        parts.append("atempo=2.0"); s /= 2.0
    while s < 0.5:
        parts.append("atempo=0.5"); s /= 0.5
    parts.append(f"atempo={s:.5f}")
    return ",".join(parts)


def _fit(src_w, src_h, W, H, mode, inp, out):
    if mode == "auto":
        ar, tar = src_w / max(1, src_h), W / H
        mode = "cover" if abs(ar - tar) / tar < 0.12 else "blur"
    if mode == "cover":
        return f"[{inp}]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[{out}]"
    if mode == "contain":
        return (f"[{inp}]scale={W}:{H}:force_original_aspect_ratio=decrease,"
                f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[{out}]")
    # blur: blurred, darkened cover-fill behind the contained frame (vertical phone clips)
    return (f"[{inp}]split[{out}_a][{out}_b];"
            f"[{out}_a]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},"
            f"gblur=sigma=12,eq=brightness=-0.12:saturation=0.85,scale={W}:{H}[{out}_bg];"
            f"[{out}_b]scale={W}:{H}:force_original_aspect_ratio=decrease[{out}_fg];"
            f"[{out}_bg][{out}_fg]overlay=(W-w)/2:(H-h)/2,setsar=1[{out}]")


class Renderer:
    def __init__(self, project, mode="preview", items_range=None, workers=3):
        self.p = project
        self.mode = mode
        self.edl = project.edl()
        if not self.edl:
            raise SystemExit("Brak edl.json w projekcie")
        self.media = project.media()
        self.clips = self.media["clips"]
        self.s = settings_of(self.edl)
        if mode == "preview":
            self.W, self.H = self.s["width"] // 2, self.s["height"] // 2
        else:
            self.W, self.H = self.s["width"], self.s["height"]
        self.FPS = int(self.s["fps"])
        self.items_all, self.total_all = compute(self.edl)
        self.range = items_range
        self.workers = workers
        self.cdir = project.cache / mode
        self.cdir.mkdir(parents=True, exist_ok=True)
        self.log = project.cache / "ffmpeg.log"
        self.hw = ["-hwaccel", "cuda"] if nvenc_available() else []

    # ------------------------------------------------------------ helpers
    def _key(self, *parts):
        h = hashlib.sha1(json.dumps([VERSION, self.mode, self.W, self.H, self.FPS, *parts],
                                    sort_keys=True, default=str).encode()).hexdigest()
        return h[:16]

    def _frames(self, dur):
        return max(1, round(dur * self.FPS))

    def _src_sig(self, cid):
        c = self.clips[cid]
        st = os.stat(c["path"])
        return [c["path"], st.st_size, int(st.st_mtime)]

    def _ffmpeg(self, args, dur, prog):
        try:
            run_ffmpeg(args, dur, prog, self.log)
        except FFmpegError:
            if self.hw and "-hwaccel" in args:
                # retry with software decode
                a = list(args)
                while "-hwaccel" in a:
                    i = a.index("-hwaccel"); del a[i:i + 2]
                run_ffmpeg(a, dur, prog, self.log)
            else:
                raise

    # ------------------------------------------------------------ segments
    def _caption(self, it, dur, key):
        cap = it.get("caption")
        if not cap:
            return None
        if isinstance(cap, str):
            cap = {"text": cap}
        label = cap.get("label", it.get("label"))
        png = self.cdir / f"cap_{key}.png"
        if not png.exists():
            caption_png(png, self.W, self.H, cap["text"], label, cap.get("position", "bottom-left"), _hex(self.s["accent"]))
        start = float(cap.get("start", 0.5))
        cdur = float(cap.get("duration", min(4.5, dur - start - 0.3)))
        return png, start, max(1.0, min(cdur, dur - start))

    def _fades(self, dur, fade_in, fade_out, label_in, label_out, audio=False):
        f = []
        flt = "afade" if audio else "fade"
        if fade_in:
            f.append(f"{flt}=t=in:st=0:d={fade_in:.3f}")
        if fade_out:
            f.append(f"{flt}=t=out:st={max(0, dur - fade_out):.3f}:d={fade_out:.3f}")
        return f"[{label_in}]{','.join(f) if f else ('anull' if audio else 'null')}[{label_out}]"

    def segment(self, it, fade_in, fade_out, st):
        """Render one timeline item to (video.mp4, audio.wav). Cached by content hash."""
        it_clean = {k: v for k, v in it.items() if k not in ("idx", "start", "end", "note") and not k.startswith("_")}
        src = self._src_sig(it["id"]) if it["type"] in ("clip", "image") else None
        bg = it.get("bg")
        if isinstance(bg, dict) and bg.get("id"):
            src = [src, self._src_sig(bg["id"])]
        look = it.get("look", self.s["look"])
        key = self._key("seg", it_clean, src, round(fade_in, 3), round(fade_out, 3), look, self.s["fit"],
                        self.s["clip_volume"], self.s["accent"])
        vout, aout = self.cdir / f"seg_{key}.mp4", self.cdir / f"seg_{key}.wav"
        if vout.exists() and aout.exists():
            return vout, aout, True

        dur = self._frames(it["dur"]) / self.FPS
        W, H, FPS = self.W, self.H, self.FPS
        args, fc = [], []
        look_f = LOOKS.get(look, look or "")
        n_in = 0
        audio_label = None

        if it["type"] == "clip":
            c = self.clips[it["id"]]
            speed = float(it.get("speed", 1) or 1)
            src_dur = float(it["out"]) - float(it["in"])
            args += self.hw + ["-ss", f"{float(it['in']):.3f}", "-t", f"{src_dur + 0.1:.3f}", "-i", c["path"]]
            n_in += 1
            pre = []
            if c.get("hdr"):
                pre.append(HDR_TONEMAP)
            if it.get("stabilize"):
                trf = self.p.cache / f"stab_{self._key('stab', self._src_sig(it['id']), it['in'], it['out'])}.trf"
                if not trf.exists():
                    st.frac(f"seg{it['idx']}", 0.02)
                    run_ffmpeg(self.hw + ["-ss", f"{float(it['in']):.3f}", "-t", f"{src_dur:.3f}", "-i", c["path"],
                                          "-vf", f"vidstabdetect=shakiness=6:accuracy=12:result='{trf}'", "-f", "null", "-"],
                               src_dur, None, self.log)
                pre.append(f"vidstabtransform=input='{trf}':smoothing=18:optzoom=1:interpol=bicubic,unsharp=5:5:0.5")
            pre.append(f"setpts=(PTS-STARTPTS)/{speed}")
            pre.append(f"fps={FPS}")
            fc.append(f"[0:v]{','.join(pre)}[pre]")
            fc.append(_fit(c["width"], c["height"], W, H, it.get("fit", self.s["fit"]), "pre", "fit"))
            base = "fit"
            vol = float(it.get("volume", self.s["clip_volume"]))
            if vol > 0 and c.get("has_audio") and 0.25 <= speed <= 8:
                fc.append(f"[0:a]asetpts=PTS-STARTPTS,{_atempo(speed) if speed != 1 else 'anull'},volume={vol},"
                          f"aformat=sample_rates=48000:channel_layouts=stereo[araw]")
                audio_label = "araw"
        elif it["type"] == "image" and it.get("style", self.s.get("image_style", "kenburns")) in ("card", "person"):
            # static framed photo (no zoom): blurred bg + card that eases in from below
            c = self.clips[it["id"]]
            layout = it.get("style", self.s.get("image_style"))
            im = ImageOps.exif_transpose(Image.open(c["path"])).convert("RGB")
            bgim, card, cx, cy = photo_layers(im, W, H, layout)
            bgp, cardp = self.cdir / f"imgbg_{key}.jpg", self.cdir / f"imgcard_{key}.png"
            bgim.save(bgp, quality=92); card.save(cardp)
            args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(bgp),
                     "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(cardp)]
            n_in += 2
            slide = 40 * W / 1920
            fc.append(f"[0:v]scale={W}:{H},setsar=1[ibg]")
            fc.append(f"[1:v]format=rgba,fade=t=in:st=0:d=0.45:alpha=1[icard]")
            fc.append(f"[ibg][icard]overlay=x={cx}:y='{cy}+{slide:.1f}*pow(max(0,1-t/0.6),3)':eval=frame:format=auto[fit]")
            base = "fit"
            if layout == "person":
                pp = self.cdir / f"person_{key}.png"
                person_png(pp, W, H, it.get("name", ""), it.get("role"), _hex(self.s["accent"]))
                args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(pp)]
                pi = n_in; n_in += 1
                fc.append(f"[{pi}:v]format=rgba,fade=t=in:st=0.25:d=0.5:alpha=1[ptx]")
                fc.append(f"[fit][ptx]overlay=x='{slide:.1f}*pow(max(0,1-(t-0.25)/0.6),3)':y=0:eval=frame:format=auto[pv]")
                base = "pv"
        elif it["type"] == "image":
            c = self.clips[it["id"]]
            kb = it.get("kenburns", "in")
            scale = 2 if kb in ("in", "out") else 1
            prep = self.cdir / f"img_{key}.jpg"
            im = ImageOps.exif_transpose(Image.open(c["path"])).convert("RGB")
            tw, th = W * scale, H * scale
            fit = it.get("fit", self.s["fit"])
            ar = im.width / im.height
            if fit == "cover" or (fit == "auto" and abs(ar - W / H) / (W / H) < 0.2):
                im = ImageOps.fit(im, (tw, th), Image.LANCZOS)
            else:
                bgim = ImageOps.fit(im, (tw // 8, th // 8)).filter(ImageFilter.GaussianBlur(6)).resize((tw, th))
                bgim = Image.eval(bgim, lambda v: int(v * 0.8))
                fg = ImageOps.contain(im, (tw, th), Image.LANCZOS)
                bgim.paste(fg, ((tw - fg.width) // 2, (th - fg.height) // 2))
                im = bgim
            im.save(prep, quality=94)
            args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(prep)]
            n_in += 1
            N = self._frames(dur)
            if kb in ("in", "out"):
                z = f"1+0.08*in/{N}" if kb == "in" else f"1.08-0.08*in/{N}"
                fc.append(f"[0:v]zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS},setsar=1[fit]")
            else:
                fc.append(f"[0:v]scale={W}:{H},setsar=1[fit]")
            base = "fit"
        else:  # title
            bg = it.get("bg", "black")
            if isinstance(bg, dict) and bg.get("id"):
                c = self.clips[bg["id"]]
                if c["type"] == "video":
                    bin_ = float(bg.get("in", 0))
                    args += self.hw + ["-ss", f"{bin_:.3f}", "-t", f"{dur + 0.1:.3f}", "-i", c["path"]]
                    pre = ([HDR_TONEMAP] if c.get("hdr") else []) + ["setpts=PTS-STARTPTS", f"fps={FPS}"]
                    fc.append(f"[0:v]{','.join(pre)}[pre]")
                    fc.append(_fit(c["width"], c["height"], W, H, "cover", "pre", "bg0"))
                else:
                    args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", c["path"]]
                    fc.append(f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[bg0]")
                blur = float(bg.get("blur", 10)) * W / 1920
                dim = float(bg.get("dim", 0.45))
                fc.append(f"[bg0]gblur=sigma={blur:.2f},eq=brightness={-dim * 0.4:.3f}:saturation=0.8,"
                          f"colorchannelmixer=rr={1 - dim * 0.5:.3f}:gg={1 - dim * 0.5:.3f}:bb={1 - dim * 0.5:.3f},vignette=PI/4[fit]")
            else:
                color = bg if isinstance(bg, str) and bg.startswith("#") else "#0b0b0d"
                args += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", f"color=c={color}:s={W}x{H}:r={FPS}"]
                fc.append("[0:v]setsar=1[fit]")
            n_in += 1
            png = self.cdir / f"title_{key}.png"
            title_png(png, W, H, it.get("text", ""), it.get("subtitle"), it.get("label"), it.get("style", "main"),
                      _hex(self.s["accent"]))
            args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(png)]
            ti = n_in; n_in += 1
            tin = float(it.get("text_in", 0.35))
            tout = max(tin + 0.5, dur - 0.55)
            fc.append(f"[{ti}:v]format=rgba,fade=t=in:st={tin:.2f}:d=0.7:alpha=1,fade=t=out:st={tout:.2f}:d=0.5:alpha=1[tx]")
            fc.append("[fit][tx]overlay=0:0:format=auto[ttl]")
            base = "ttl"
            look_f = ""

        if look_f:
            fc.append(f"[{base}]{look_f}[lk]"); base = "lk"

        capinfo = self._caption(it, dur, key) if it["type"] != "title" else None
        if capinfo:
            png, cs, cd = capinfo
            args += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(png)]
            ci = n_in; n_in += 1
            fc.append(f"[{ci}:v]format=rgba,fade=t=in:st={cs:.2f}:d=0.5:alpha=1,"
                      f"fade=t=out:st={cs + cd - 0.5:.2f}:d=0.5:alpha=1[cap]")
            fc.append(f"[{base}][cap]overlay=0:0:format=auto[cv]"); base = "cv"

        fc.append(self._fades(dur, fade_in, fade_out, base, "vf"))
        fc.append("[vf]format=yuv420p[v]")

        if audio_label is None:
            args += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
            audio_label = f"{n_in}:a"; n_in += 1
        fc.append(f"[{audio_label}]apad,atrim=0:{dur:.4f}[ap]")
        fc.append(self._fades(dur, fade_in, fade_out, "ap", "a", audio=True))

        tmpv, tmpa = vout.with_suffix(".part.mp4"), aout.with_suffix(".part.wav")
        args += ["-filter_complex", ";".join(fc),
                 "-map", "[v]", *video_codec_args(self.mode), "-frames:v", str(self._frames(dur)), "-an", str(tmpv),
                 "-map", "[a]", "-c:a", "pcm_s16le", "-ar", "48000", "-t", f"{dur:.4f}", str(tmpa)]
        self._ffmpeg(args, dur, lambda f: st.frac(f"seg{it['idx']}", f))
        os.replace(tmpv, vout); os.replace(tmpa, aout)
        return vout, aout, False

    # ------------------------------------------------------------ blocks
    def block(self, segs, items, st, bkey):
        """Join segments linked by xfade transitions into one clip."""
        if len(segs) == 1:
            return segs[0]
        key = self._key("blk2", [str(v) for v, _ in segs], [(i["_tname"], i["_td"]) for i in items[1:]])
        vout, aout = self.cdir / f"blk_{key}.mp4", self.cdir / f"blk_{key}.wav"
        if vout.exists() and aout.exists():
            return vout, aout
        args, fc = [], []
        for v, _ in segs:
            args += ["-i", str(v)]
        for _, a in segs:
            args += ["-i", str(a)]
        n = len(segs)
        L = self._frames(items[0]["dur"]) / self.FPS
        prev_v, prev_a = "0:v", f"{n}:a"
        for k in range(1, n):
            td = self._frames(items[k]["_td"]) / self.FPS
            off = L - td
            fc.append(f"[{prev_v}][{k}:v]xfade=transition={items[k]['_tname']}:duration={td:.4f}:offset={off:.4f}[x{k}]")
            fc.append(f"[{prev_a}][{n + k}:a]acrossfade=d={td:.4f}:c1=tri:c2=tri[y{k}]")
            prev_v, prev_a = f"x{k}", f"y{k}"
            L = L + self._frames(items[k]["dur"]) / self.FPS - td
        tmpv, tmpa = vout.with_suffix(".part.mp4"), aout.with_suffix(".part.wav")
        args += ["-filter_complex", ";".join(fc),
                 # xfade can emit one extra frame; cap to the exact frame count so blocks never drift
                 "-map", f"[{prev_v}]", *video_codec_args(self.mode), "-frames:v", str(round(L * self.FPS)), "-an", str(tmpv),
                 "-map", f"[{prev_a}]", "-c:a", "pcm_s16le", "-t", f"{L:.4f}", str(tmpa)]
        self._ffmpeg(args, L, lambda f: st.frac(bkey, f))
        os.replace(tmpv, vout); os.replace(tmpa, aout)
        return vout, aout

    # ------------------------------------------------------------ music
    def music_graph(self, total, shift, first_in):
        tracks = []
        at_cursor = 0.0
        xf = float(self.s["music_crossfade"])
        for i, m in enumerate(self.edl.get("music", [])):
            path = self.p.resolve_audio(m["file"])
            mlen = media_duration(path)
            a_in = float(m.get("in", 0))
            a_out = float(m.get("out", mlen) or mlen)
            ln = a_out - a_in
            at = float(m["at"]) if "at" in m else (at_cursor if i == 0 else at_cursor - xf)
            at_cursor = at + ln
            fi = float(m.get("fade_in", 0.5 if at <= 0 else xf))
            fo = float(m.get("fade_out", xf))
            at -= shift
            if at < 0:
                a_in -= at; ln += at; at = 0; fi = 0.3 if not first_in else fi
            if at >= total or ln <= 0.2:
                continue
            if at + ln > total:
                ln = total - at
                fo = max(0.3, min(ln / 2, float(self.s["end_fade"]) if shift == 0 else 0.3))
            tracks.append(dict(path=path, a_in=a_in, ln=ln, at=at, fi=fi, fo=fo, vol=float(m.get("volume", 1.0))))
        return tracks

    # ------------------------------------------------------------ main
    def run(self):
        p = self.p
        errs, warns = validate(self.edl, self.media)
        if errs:
            raise SystemExit("EDL ma błędy:\n  " + "\n  ".join(errs))
        items = self.items_all
        a, b = 0, len(items) - 1
        if self.range:
            a, b = max(0, self.range[0]), min(len(items) - 1, self.range[1])
        items = items[a:b + 1]
        if not items:
            raise SystemExit("Pusty zakres")
        shift = items[0]["start"] if a > 0 else 0.0
        end_fade = float(self.s["end_fade"])

        # fades: dip-to-black around 'fade' transitions, intro fade, end fade
        plan = []
        for k, it in enumerate(items):
            gi = it["idx"]
            nxt = self.items_all[gi + 1] if gi + 1 < len(self.items_all) else None
            fin = it["_td"] / 2 if it["_tkind"] == "fade" else (0.8 if gi == 0 else 0.0)
            fout = nxt["_td"] / 2 if nxt is not None and nxt["_tkind"] == "fade" else 0.0
            if nxt is None:
                fout = min(end_fade, it["dur"] / 2)
            plan.append((it, fin, fout))

        n_blocks = sum(1 for k, it in enumerate(items) if k == 0 or it["_tkind"] != "xfade")
        st = Status(p, f"render-{self.mode}", total_steps=len(items) + n_blocks + 2)
        st.s["range"] = [a, b]
        label = f"{self.mode}" + (f" #{a}-{b}" if self.range else "")
        event(p, f"Render {label}: {len(items)} elementów, {fmt_time(items[-1]['end'] - shift)}, {self.W}x{self.H}"
              + (" (NVENC)" if nvenc_available() else ""))
        for w in warns:
            event(p, w, "warn")
        st.stage("Renderowanie ujęć", f"{len(items)} elementów")
        t0 = time.time()

        segs = {}
        cached = 0
        with ThreadPoolExecutor(self.workers) as ex:
            futs = {}
            for it, fin, fout in plan:
                key = f"seg{it['idx']}"
                st.begin(key, f"#{it['idx']} {it['type']} {it.get('id') or it.get('text', '')}", it["idx"])
                futs[ex.submit(self.segment, it, fin, fout, st)] = it
            for fut in as_completed(futs):
                it = futs[fut]
                try:
                    v, au, hit = fut.result()
                except Exception as e:
                    st.finish("error", f"Błąd przy #{it['idx']}: {str(e)[:400]}")
                    event(p, f"Błąd renderu #{it['idx']}: {str(e)[:1500]}", "error")
                    raise
                cached += hit
                segs[it["idx"]] = (v, au)
                st.end(f"seg{it['idx']}")
        if cached:
            event(p, f"{cached} z {len(items)} ujęć z cache (bez zmian)")

        st.stage("Przejścia", f"{n_blocks} bloków")
        st.s["steps_done"] = len(items); st.s["steps_total"] = len(items) + n_blocks + 2
        groups = []
        for k, it in enumerate(items):
            if k == 0 or it["_tkind"] != "xfade":
                groups.append([it])
            else:
                groups[-1].append(it)
        blocks = []
        for gi, g in enumerate(groups):
            bk = f"blk{gi}"
            st.begin(bk, f"Przejścia #{g[0]['idx']}–#{g[-1]['idx']}", g[0]["idx"])
            blocks.append(self.block([segs[i["idx"]] for i in g], g, st, bk))
            st.end(bk)

        st.stage("Składanie i miks audio", "concat + muzyka + normalizacja głośności")
        work = self.cdir / "work"
        work.mkdir(exist_ok=True)
        lv, la = work / "v.txt", work / "a.txt"
        lv.write_text("".join(f"file '{v}'\n" for v, _ in blocks))
        la.write_text("".join(f"file '{au}'\n" for _, au in blocks))
        body_v, body_a = work / "body.mp4", work / "body.wav"
        st.begin("concat", "Łączenie ujęć")
        run_ffmpeg(["-f", "concat", "-safe", "0", "-i", lv, "-c", "copy", body_v], None, None, self.log)
        run_ffmpeg(["-f", "concat", "-safe", "0", "-i", la, "-c:a", "pcm_s16le", body_a], None, None, self.log)
        st.end("concat")
        total = media_duration(body_v)

        tracks = self.music_graph(total, shift, a == 0)
        args = ["-i", body_v, "-i", body_a]
        fc = ["[1:a]aformat=sample_rates=48000:channel_layouts=stereo[clipa]"]
        mix = ["[clipa]"]
        for k, t in enumerate(tracks):
            args += ["-i", t["path"]]
            ms = int(t["at"] * 1000)
            fc.append(f"[{k + 2}:a]atrim=start={t['a_in']:.3f}:end={t['a_in'] + t['ln']:.3f},asetpts=PTS-STARTPTS,"
                      f"aformat=sample_rates=48000:channel_layouts=stereo,"
                      f"afade=t=in:d={t['fi']:.2f},afade=t=out:st={max(0, t['ln'] - t['fo']):.3f}:d={t['fo']:.2f},"
                      f"volume={t['vol']},adelay={ms}|{ms}[m{k}]")
            mix.append(f"[m{k}]")
        fc.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first,atrim=0:{total:.4f}[mx]")
        target = float(self.s["loudness"])
        loud = f"loudnorm=I={target}:TP=-1.5:LRA=11"
        if self.mode == "final" and tracks:
            # two-pass loudness normalisation for an accurate, transparent level
            st.begin("loud1", "Pomiar głośności (pass 1)")
            err = run_ffmpeg(args + ["-filter_complex", ";".join(fc + [f"[mx]{loud}:print_format=json[o]"]),
                                     "-map", "[o]", "-f", "null", "-"], total,
                             lambda f: st.frac("loud1", f), loglevel="info")
            st.end("loud1")
            m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", err, re.S)
            if m:
                j = json.loads(m.group(0))
                loud += (f":measured_I={j['input_i']}:measured_TP={j['input_tp']}:measured_LRA={j['input_lra']}"
                         f":measured_thresh={j['input_thresh']}:offset={j['target_offset']}:linear=true")
        fc.append(f"[mx]{loud},aresample=48000[aout]")

        p.renders.mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        safe = re.sub(r"[^\w-]+", "_", self.edl.get("title") or p.name).strip("_")[:40] or p.name
        out = p.renders / f"{safe}_{self.mode}{'_%d-%d' % (a, b) if self.range else ''}_{stamp}.mp4"
        meta_args = []
        chs = [c for c in chapters(self.items_all) if a == 0 and not self.range]
        if chs:
            mf = work / "chapters.txt"
            lines = [";FFMETADATA1", f"title={self.edl.get('title', p.name)}"]
            for k, (t, name) in enumerate(chs):
                end = chs[k + 1][0] if k + 1 < len(chs) else total
                lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={int(t * 1000)}", f"END={int(end * 1000)}", f"title={name}"]
            mf.write_text("\n".join(lines) + "\n")
            args += ["-f", "ffmetadata", "-i", mf]
            meta_args = ["-map_metadata", str(len(tracks) + 2), "-map_chapters", str(len(tracks) + 2)]
        st.begin("mix", "Miks muzyki i eksport")
        run_ffmpeg(args + ["-filter_complex", ";".join(fc), "-map", "0:v", "-map", "[aout]", *meta_args,
                           "-c:v", "copy", "-c:a", "aac", "-b:a", "320k" if self.mode == "final" else "160k",
                           "-movflags", "+faststart", out], total, lambda f: st.frac("mix", f), self.log)
        st.end("mix")

        if chs:
            (out.with_suffix(".chapters.txt")).write_text(
                "\n".join(f"{fmt_time(t).split('.')[0] if t < 3600 else fmt_time(t)} {name}" for t, name in chs) + "\n")
        shutil.copy(p.edl_path, out.with_suffix(".edl.json"))
        idx = read_json(p.renders / "index.json", [])
        rec = {"file": out.name, "mode": self.mode, "range": [a, b] if self.range else None, "created": time.time(),
               "duration": round(total, 2), "size": out.stat().st_size, "render_time": round(time.time() - t0, 1),
               "edl_version": self.edl.get("version")}
        idx.append(rec)
        write_json(p.renders / "index.json", idx)
        msg = f"Gotowe: {out.name} ({fmt_time(total)}, {out.stat().st_size / 1e6:.0f} MB, {time.time() - t0:.0f}s)"
        st.finish("done", msg, output=out.name)
        event(p, msg, "done", file=out.name)
        return out


def still(project, idx, at=None):
    """Quick look at one item: render just its segment (preview) and grab a frame to PNG."""
    r = Renderer(project, "preview")
    it = r.items_all[idx]
    st = Status(project, "still", 1)
    st.begin(f"seg{idx}", f"Klatka #{idx}", idx)
    v, _, _ = r.segment(it, 0, 0, st)
    st.end(f"seg{idx}")
    t = it["dur"] * 0.6 if at is None else at
    out = project.dir / "stills" / f"item{idx:03d}.jpg"
    out.parent.mkdir(exist_ok=True)
    run_ffmpeg(["-ss", f"{t:.2f}", "-i", v, "-frames:v", "1", "-q:v", "2", out])
    st.finish("done", f"Klatka #{idx} → {out.name}")
    return out
