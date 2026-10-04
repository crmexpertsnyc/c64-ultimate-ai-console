#!/usr/bin/env bash
# Run every quality gate: backend lint + tests, frontend typecheck + lint + build.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python
"$PY" -m ruff check app tests
"$PY" -m pytest -q
cd "$ROOT/frontend"
npx tsc -b
npx eslint .
npx vite build
echo "All checks passed."
