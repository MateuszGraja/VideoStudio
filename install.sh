#!/usr/bin/env bash
# Video Studio installer (Ubuntu/Debian). Safe to re-run.
#   ./install.sh               venv + Python packages + `vs` command + Claude Code skill
#   ./install.sh --no-beat     skip Beat This!/PyTorch (small install, librosa beat tracking only)
#   ./install.sh --desktop     also create desktop + app-menu shortcuts (GNOME)
set -euo pipefail
ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$ROOT"
NO_BEAT=0; DESKTOP=0
for a in "$@"; do case "$a" in --no-beat) NO_BEAT=1;; --desktop) DESKTOP=1;; *) echo "nieznana opcja: $a"; exit 2;; esac; done

say() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "Brak programu: $1 — $2"; exit 1; }; }

say "Sprawdzam programy systemowe"
need python3 "sudo apt install python3 python3-venv"
need ffmpeg  "sudo apt install ffmpeg"
need ffprobe "sudo apt install ffmpeg"
need curl    "sudo apt install curl"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo "Potrzebny Python >= 3.10"; exit 1; }
python3 -m venv --help >/dev/null 2>&1 || { echo "Brak modułu venv: sudo apt install python3-venv"; exit 1; }
ENCODERS="$(ffmpeg -hide_banner -encoders 2>/dev/null || true)"  # no pipe into grep -q: SIGPIPE + pipefail = false negative
if grep -q h264_nvenc <<<"$ENCODERS"; then
  echo "   ffmpeg ma NVENC (kodowanie na karcie NVIDIA) ✓"
else
  echo "   ffmpeg bez NVENC — render pójdzie na CPU (libx264), wolniej, ale działa"
fi

say "Środowisko Python (.venv)"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
if [ "$NO_BEAT" = 1 ]; then
  grep -v '^beat-this' requirements.txt > /tmp/vs-req.txt
  .venv/bin/pip install -q -r /tmp/vs-req.txt
else
  echo "   (Beat This! ściąga PyTorch — przy wolnym łączu to może potrwać)"
  .venv/bin/pip install -q -r requirements.txt
fi

say "Katalogi robocze"
mkdir -p projects music

say "Komenda vs → ~/.local/bin/vs"
mkdir -p "$HOME/.local/bin"
ln -sf "$ROOT/bin/vs" "$HOME/.local/bin/vs"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "   Dodaj ~/.local/bin do PATH (np. w ~/.bashrc: export PATH=\"\$HOME/.local/bin:\$PATH\")";; esac

say "Skill dla Claude Code → ~/.claude/skills/video-studio"
mkdir -p "$HOME/.claude/skills"
if [ -e "$HOME/.claude/skills/video-studio" ] && [ ! -L "$HOME/.claude/skills/video-studio" ]; then
  mv "$HOME/.claude/skills/video-studio" "$HOME/.claude/video-studio-skill.bak.$(date +%s)"  # outside skills/ so it is not loaded twice
fi
ln -sfn "$ROOT/claude-skill/video-studio" "$HOME/.claude/skills/video-studio"

if [ "$DESKTOP" = 1 ]; then
  say "Skróty na pulpicie i w menu"
  DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
  mkdir -p "$DESK" "$HOME/.local/share/applications"
  for variant in "" "--claude"; do
    if [ -z "$variant" ]; then f=video-studio.desktop; name="Video Studio"; c="Panel montażu (podgląd, rendery, uwagi)";
    else f=video-studio-claude.desktop; name="Video Studio + Claude"; c="Panel + Claude Code wznawiający ostatnią sesję"; fi
    cat > "$HOME/.local/share/applications/$f" <<DESK
[Desktop Entry]
Type=Application
Name=$name
Comment=$c
Exec=$ROOT/start.sh $variant
Icon=$ROOT/panel/icon.png
Terminal=false
Categories=AudioVideo;Video;
DESK
    cp "$HOME/.local/share/applications/$f" "$DESK/$f"
    chmod +x "$DESK/$f"
    gio set "$DESK/$f" metadata::trusted true 2>/dev/null || true
  done
fi

say "Gotowe. Start panelu: ./start.sh  (albo: vs serve → http://127.0.0.1:8765)"
