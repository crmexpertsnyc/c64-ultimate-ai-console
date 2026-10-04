#!/usr/bin/env bash
# Start backend (API on :8064) and frontend dev server (:5173) for development.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
[ -d .venv ] || python3 -m venv .venv
PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python
"$PY" -m pip install -q -e ".[dev,mcp]"
( cd "$ROOT/frontend" && { [ -d node_modules ] || npm ci; } && npm run dev ) &
FRONT=$!
trap 'kill $FRONT 2>/dev/null' EXIT
"$PY" -m uvicorn app.main:app --reload --host 0.0.0.0 --port "${WEB_PORT:-8064}"
