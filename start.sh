#!/usr/bin/env bash
# Video Studio launcher: starts the panel in the background (if it isn't running) and opens it in the browser.
# Usage: start.sh            -> panel only
#        start.sh --claude   -> panel + terminal with Claude Code resuming the last editing session
# Env:   VS_PORT (default 8765), VS_CLAUDE_DIR (folder where the Claude conversation runs;
#        default: the first footage folder of the active project), VS_TERMINAL (default gnome-terminal)
ROOT="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$ROOT" || exit 1
PORT="${VS_PORT:-8765}"
URL="http://127.0.0.1:$PORT"
if ! curl -s -o /dev/null "$URL/api/projects"; then
  nohup "$ROOT/.venv/bin/python" -m studio.cli serve --port "$PORT" >>"$ROOT/panel.log" 2>&1 &
  for _ in $(seq 1 40); do curl -s -o /dev/null "$URL/api/projects" && break; sleep 0.25; done
fi
xdg-open "$URL" >/dev/null 2>&1 &
if [ "$1" = "--claude" ]; then
  DIR="${VS_CLAUDE_DIR:-}"
  if [ -z "$DIR" ] && [ -f "$ROOT/projects/.current" ]; then
    DIR="$("$ROOT/.venv/bin/python" -c "import json,sys; print((json.load(open(sys.argv[1])).get('sources') or [''])[0])" \
           "$ROOT/projects/$(cat "$ROOT/projects/.current")/project.json" 2>/dev/null)"
  fi
  [ -d "$DIR" ] || DIR="$ROOT"
  CLAUDE="$(command -v claude || echo "$HOME/.local/bin/claude")"
  # --continue resumes the most recent conversation started in $DIR (the editing session keeps its context)
  "${VS_TERMINAL:-gnome-terminal}" --title="Video Studio — Claude" --working-directory="$DIR" \
    -- bash -lc "\"$CLAUDE\" --continue || \"$CLAUDE\"; exec bash"
fi
