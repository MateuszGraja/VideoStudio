# Video Studio

Lokalne narzędzie do montażu: Claude Code steruje komendą `vs`, ffmpeg (NVENC) renderuje, panel webowy pokazuje postęp.
Instrukcja pracy dla Claude: skill `video-studio` (`claude-skill/video-studio/SKILL.md`, podlinkowany do `~/.claude/skills/`).

## Struktura
- `studio/` — kod (`cli.py` komendy, `ingest.py`, `analyze.py`, `render.py`, `graphics.py`, `timeline.py`, `music.py`, `server.py`)
- `panel/index.html` — panel (vanilla JS, odpytuje `/api/p/<projekt>/state` co 1,5 s; `/summary` = baner „Ostatni stan”)
- `projects/<nazwa>/` — dane projektu (poza gitem): `media.json`, `edl.json`, `sheets/`, `thumbs/`, `analysis/`,
  `music/`, `music_analysis/`, `cache/`, `renders/`, `events.jsonl`, `status.json`, `feedback.json`, opcjonalnie `tools/build_edl.py`
- `fonts/` (Montserrat, Inter — OFL), `music/` (wspólna biblioteka muzyki, poza gitem)
- `bin/vs` (wrapper → `~/.local/bin/vs`), `start.sh` (panel + opcjonalnie Claude), `install.sh`

## Uruchomienie panelu
`./start.sh` albo `vs serve` → http://127.0.0.1:8765
