"""vs — command line for the video studio. Run `vs -h` for the list of commands."""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .core import (CURRENT, PROJECTS, ROOT, event, fmt_time, get_project, list_projects, read_json, write_json)


def cmd_new(a):
    d = PROJECTS / a.name
    if (d / "project.json").exists():
        raise SystemExit(f"Projekt {a.name} już istnieje")
    d.mkdir(parents=True)
    (d / "music").mkdir()
    write_json(d / "project.json", {"name": a.name, "created": time.time(),
                                    "sources": [str(Path(s).expanduser().resolve()) for s in a.source]})
    CURRENT.write_text(a.name)
    p = get_project(a.name)
    event(p, f"Utworzono projekt {a.name}")
    print(f"Projekt: {d}\nAktywny projekt ustawiony. Dalej: vs ingest")


def cmd_use(a):
    get_project(a.name)
    CURRENT.write_text(a.name)
    print(f"Aktywny projekt: {a.name}")


def cmd_projects(a):
    cur = (CURRENT.read_text().strip() if CURRENT.exists() else "")
    for n in list_projects():
        print(("* " if n == cur else "  ") + n)


def cmd_ingest(a):
    from .ingest import ingest
    ingest(get_project(a.project), a.add or ())


def cmd_analyze(a):
    from .analyze import analyze
    analyze(get_project(a.project), a.ids or None, a.force)


def cmd_list(a):
    from .analyze import describe_best
    p = get_project(a.project)
    clips = p.media()["clips"]
    day = None
    grp = None
    order = sorted(clips.items(), key=lambda kv: (kv[1].get("group", ""), kv[1].get("event", ""), kv[1].get("day", ""), kv[1]["date"]))
    for cid, c in (clips.items() if a.flat else order):
        if a.untagged and c.get("desc"):
            continue
        if a.chapter is not None and c.get("chapter") != a.chapter:
            continue
        if a.date and not c["date"].startswith(a.date):
            continue
        if a.group and c.get("group", "").lower() != a.group.lower():
            continue
        if not a.flat and (c.get("group"), c.get("event"), c.get("day")) != grp:
            grp = (c.get("group"), c.get("event"), c.get("day"))
            day_s = f" / {c['day']}" if c.get("day") else ""
            print(f"\n== {c.get('group') or '(główny folder)'} / {c.get('event') or '-'}{day_s} ==")
        dur = fmt_time(c["duration"]) if c["type"] == "video" else "foto"
        q = f" q={c['quality']}" if c.get("quality") is not None else ""
        fl = f" [{', '.join(c.get('flags', []))}]" if c.get("flags") else ""
        meta = []
        if c.get("chapter") is not None: meta.append(f"rozdz={c['chapter']}")
        if c.get("rating"): meta.append("★" * int(c["rating"]))
        if c.get("tags"): meta.append("#" + " #".join(c["tags"]))
        miss = " [BRAK PLIKU]" if c.get("missing") else ""
        print(f"{cid}{miss}  {c['date'][:16]}  {dur:>8}  {c['width']}x{c['height']}{q}{fl}  {' '.join(meta)}")
        if c.get("desc"):
            print(f"        {c['desc']}")
        if a.best and c.get("best"):
            print(f"        najlepsze: {describe_best(c)}")
    vids = [c for c in clips.values() if c["type"] == "video"]
    print(f"\n{len(clips)} pozycji, {len(vids)} wideo, łącznie {fmt_time(sum(c['duration'] for c in vids))}; "
          f"opisanych: {sum(1 for c in clips.values() if c.get('desc'))}")


def cmd_info(a):
    p = get_project(a.project)
    c = p.media()["clips"].get(a.id)
    if not c:
        raise SystemExit("Brak klipu")
    an = read_json(p.analysis / f"{a.id}.json")
    print(json.dumps({k: v for k, v in c.items()}, ensure_ascii=False, indent=2))
    print(f"arkusz klatek: {p.sheets / (a.id + '.jpg')}")
    if an and "score" in an:
        print("\nsekunda: ocena (ostrość/drgania/jasność)")
        for i, sc in enumerate(an["score"]):
            print(f"{i:>4}s  {'▇' * int(sc * 20):<20} {sc:.2f}  sh={an['shake'][i]:.2f} br={an['brightness'][i]:.0f}")
        if an["cuts"]:
            print("cięcia wewnątrz klipu:", an["cuts"])


def cmd_sheet(a):
    from .ingest import contact_sheet
    p = get_project(a.project)
    c = p.media()["clips"][a.id]
    end = a.end if a.end is not None else c["duration"]
    out = p.sheets / "zoom" / f"{a.id}_{a.start:.1f}-{end:.1f}.jpg"
    contact_sheet(c, c["path"], out, a.start, end, a.n)
    print(out)


def cmd_tag(a):
    p = get_project(a.project)
    m = p.media()
    for cid in a.ids:
        c = m["clips"].get(cid)
        if not c:
            print(f"brak {cid}", file=sys.stderr); continue
        if a.desc is not None: c["desc"] = a.desc
        if a.chapter is not None: c["chapter"] = a.chapter
        if a.rating is not None: c["rating"] = a.rating
        if a.tags is not None: c["tags"] = [t.strip() for t in a.tags.split(",") if t.strip()]
    p.save_media(m)
    print(f"Zapisano {len(a.ids)} klip(y)")


def cmd_tagjson(a):
    """Bulk tagging: JSON {id: {desc, chapter, rating, tags}} from a file or stdin."""
    p = get_project(a.project)
    data = json.load(open(a.file) if a.file != "-" else sys.stdin)
    m = p.media()
    for cid, v in data.items():
        if cid in m["clips"]:
            m["clips"][cid].update({k: v[k] for k in ("desc", "chapter", "rating", "tags") if k in v})
    p.save_media(m)
    event(p, f"Opisano {len(data)} klipów")
    print(f"Zapisano {len(data)} klipów")


def cmd_chapters(a):
    p = get_project(a.project)
    meta = p.meta
    if a.set:
        meta["chapters"] = json.loads(a.set)
        write_json(p.meta_path, meta)
    for i, ch in enumerate(meta.get("chapters", [])):
        print(f"{i}: {ch}")


def cmd_beats(a):
    from .music import analyze_music, energy_summary
    p = get_project(a.project)
    r = analyze_music(p, a.file, force=a.force)
    print(f"{Path(r['file']).name}: {r['tempo']} BPM, {fmt_time(r['duration'])}, takty co ~{60 / r['tempo'] * 4:.2f}s, silnik: {r.get('engine', 'librosa')}")
    print("mocne akcenty (dobre miejsca na cięcia/wejścia):", ", ".join(f"{t:.2f}" for t in r["hits"]))
    if a.bars:
        print("początki taktów:", ", ".join(f"{t:.2f}" for t in r["bars"]))
    print(energy_summary(r))


def cmd_music(a):
    from .music import list_music
    p = get_project(a.project)
    tr = list_music(p)
    if not tr:
        print("Brak muzyki. Użytkownik dodaje ją w panelu (zakładka Muzyka) albo do projects/<n>/music/.")
    for t in tr:
        d = fmt_time(t["duration"]) if t["duration"] else "?"
        bpm = f"{t['tempo']:.0f} BPM" if t["tempo"] else "nieprzeanalizowany (vs beats)"
        print(f"{t['file']}  [{t['scope']}]  {d}  {bpm}" + (f"  · w filmie x{t['used']}" if t["used"] else ""))
        if t["note"]:
            print(f"    uwaga użytkownika: {t['note']}")


def cmd_timeline(a):
    from .timeline import chapters, compute, table, validate
    p = get_project(a.project)
    edl, media = p.edl(), p.media()
    errs, warns = validate(edl, media)
    print(table(edl, media))
    items, total = compute(edl)
    chs = chapters(items)
    if chs:
        print("\nRozdziały:")
        for t, n in chs:
            print(f"  {fmt_time(t)}  {n}")
    for w in warns: print("UWAGA:", w)
    for e in errs: print("BŁĄD:", e)
    if errs:
        sys.exit(1)


def _range(s):
    if not s:
        return None
    a, _, b = s.partition("-")
    return int(a), int(b or a)


def cmd_render(a):
    from .render import Renderer
    p = get_project(a.project)
    mode = "final" if a.final else "preview"
    out = Renderer(p, mode, _range(a.items), a.workers).run()
    print(out)


def cmd_still(a):
    from .render import still
    print(still(get_project(a.project), a.idx, a.at))


def cmd_note(a):
    p = get_project(a.project)
    event(p, " ".join(a.text), "claude")


def cmd_feedback(a):
    p = get_project(a.project)
    fb = read_json(p.feedback_path, [])
    if a.resolve:
        ids = set(a.resolve)
        for f in fb:
            if "all" in ids or str(f["id"]) in ids:
                f["resolved"] = True
        write_json(p.feedback_path, fb)
    open_ = [f for f in fb if not f.get("resolved")]
    if not open_:
        print("Brak nowych uwag od użytkownika.")
    edl = p.edl() or {}
    tl = edl.get("timeline", [])
    for f in open_:
        where = ""
        if f.get("item") is not None:
            it = tl[f["item"]] if f["item"] < len(tl) else {}
            where = f" [#{f['item']} {it.get('type', '')} {it.get('id') or it.get('text', '')}]"
        elif f.get("clip"):
            where = f" [klip {f['clip']}]"
        elif f.get("time") is not None:
            where = f" [@{fmt_time(f['time'])} w {f.get('render', '')}]"
        print(f"({f['id']}){where} {f['text']}")


def cmd_status(a):
    p = get_project(a.project)
    s = read_json(p.status_path, {})
    print(json.dumps(s, ensure_ascii=False, indent=2))


def cmd_serve(a):
    from .server import serve
    serve(a.port, a.host)


def main():
    ap = argparse.ArgumentParser(prog="vs", description="Video Studio — montaż z Claude Code + ffmpeg")
    ap.add_argument("-p", "--project", help="nazwa projektu (domyślnie aktywny)")
    sp = ap.add_subparsers(dest="cmd", required=True)

    x = sp.add_parser("new", help="nowy projekt"); x.add_argument("name"); x.add_argument("--source", action="append", default=[]); x.set_defaults(f=cmd_new)
    x = sp.add_parser("use", help="ustaw aktywny projekt"); x.add_argument("name"); x.set_defaults(f=cmd_use)
    x = sp.add_parser("projects", help="lista projektów"); x.set_defaults(f=cmd_projects)
    x = sp.add_parser("ingest", help="skanuj materiał, miniatury, arkusze klatek"); x.add_argument("--add", action="append"); x.set_defaults(f=cmd_ingest)
    x = sp.add_parser("analyze", help="analiza jakości ujęć"); x.add_argument("ids", nargs="*"); x.add_argument("--force", action="store_true"); x.set_defaults(f=cmd_analyze)
    x = sp.add_parser("list", help="lista klipów"); x.add_argument("--untagged", action="store_true"); x.add_argument("--chapter", type=int)
    x.add_argument("--date"); x.add_argument("--group"); x.add_argument("--best", action="store_true"); x.add_argument("--flat", action="store_true"); x.set_defaults(f=cmd_list)
    x = sp.add_parser("info", help="szczegóły klipu + ocena sekunda po sekundzie"); x.add_argument("id"); x.set_defaults(f=cmd_info)
    x = sp.add_parser("sheet", help="arkusz klatek dla zakresu czasu"); x.add_argument("id"); x.add_argument("--start", type=float, default=0.0)
    x.add_argument("--end", type=float); x.add_argument("-n", type=int, default=12); x.set_defaults(f=cmd_sheet)
    x = sp.add_parser("tag", help="opisz klip(y)"); x.add_argument("ids", nargs="+"); x.add_argument("--desc"); x.add_argument("--chapter", type=int)
    x.add_argument("--rating", type=int); x.add_argument("--tags"); x.set_defaults(f=cmd_tag)
    x = sp.add_parser("tagjson", help="hurtowe opisy z JSON"); x.add_argument("file"); x.set_defaults(f=cmd_tagjson)
    x = sp.add_parser("chapters", help="pokaż/ustaw listę rozdziałów (JSON)"); x.add_argument("--set"); x.set_defaults(f=cmd_chapters)
    x = sp.add_parser("beats", help="analiza muzyki (BPM, beaty, energia)"); x.add_argument("file"); x.add_argument("--bars", action="store_true"); x.add_argument("--force", action="store_true"); x.set_defaults(f=cmd_beats)
    x = sp.add_parser("music", help="lista muzyki projektu (+ uwagi użytkownika)"); x.set_defaults(f=cmd_music)
    x = sp.add_parser("timeline", help="pokaż i zweryfikuj edl.json"); x.set_defaults(f=cmd_timeline)
    x = sp.add_parser("render", help="renderuj (domyślnie podgląd 540p)"); x.add_argument("--final", action="store_true")
    x.add_argument("--items", help="zakres elementów, np. 10-25"); x.add_argument("--workers", type=int, default=3); x.set_defaults(f=cmd_render)
    x = sp.add_parser("still", help="klatka z elementu osi czasu"); x.add_argument("idx", type=int); x.add_argument("--at", type=float); x.set_defaults(f=cmd_still)
    x = sp.add_parser("note", help="wiadomość do panelu (co teraz robię)"); x.add_argument("text", nargs="+"); x.set_defaults(f=cmd_note)
    x = sp.add_parser("feedback", help="uwagi użytkownika z panelu"); x.add_argument("--resolve", nargs="*"); x.set_defaults(f=cmd_feedback)
    x = sp.add_parser("status", help="stan bieżącego zadania"); x.set_defaults(f=cmd_status)
    x = sp.add_parser("serve", help="uruchom panel"); x.add_argument("--port", type=int, default=8765); x.add_argument("--host", default="127.0.0.1"); x.set_defaults(f=cmd_serve)

    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
