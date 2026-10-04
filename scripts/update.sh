#!/usr/bin/env bash
# Update the C64 Ultimate AI Console (Linux / macOS):  bash scripts/update.sh [--repo owner/name]
# A git checkout is updated with `git pull --ff-only`; otherwise the latest GitHub release is downloaded.
# backend/data (settings, library, saves, backups) and backend/.venv are never replaced.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="${C64_CONSOLE_REPO:-crmexpertsnyc/c64-ultimate-ai-console}"
[ "${1:-}" = "--repo" ] && REPO="${2:-}"
if [ -z "$REPO" ] && [ -f "$ROOT/backend/data/settings.json" ]; then
  REPO="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("UPDATE_REPO",""))' "$ROOT/backend/data/settings.json" 2>/dev/null || true)"
fi
mkdir -p "$ROOT/backend/data/logs"
log() { printf '[%s] %s\n' "$(date +%FT%T)" "$*" | tee -a "$ROOT/backend/data/logs/update.log"; }

log "update started in $ROOT"
if [ -d "$ROOT/.git" ] && command -v git >/dev/null 2>&1; then
  cd "$ROOT"
  [ -z "$(git status --porcelain --untracked-files=no)" ] || { log "local changes — commit or stash them first"; exit 1; }
  git pull --ff-only
  BUILD=1
else
  [ -n "$REPO" ] || { log "no update source: set UPDATE_REPO in Settings or pass --repo owner/name"; exit 1; }
  api="$(curl -fsSL -H 'User-Agent: c64-console-updater' "https://api.github.com/repos/$REPO/releases/latest")"
  url="$(printf '%s' "$api" | python3 -c 'import json,sys;d=json.load(sys.stdin);a=[x["browser_download_url"] for x in d.get("assets",[]) if x["name"].endswith((".tar.gz",".zip"))];print(a[0] if a else d["tarball_url"])')"
  tmp="$(mktemp -d)"; curl -fsSL "$url" -o "$tmp/release"; mkdir -p "$tmp/x"
  if unzip -tq "$tmp/release" >/dev/null 2>&1; then unzip -q "$tmp/release" -d "$tmp/x"; else tar -xzf "$tmp/release" -C "$tmp/x"; fi
  src="$(find "$tmp/x" -mindepth 1 -maxdepth 1 -type d | head -n1)"
  rm -rf "$src/backend/data" "$src/backend/.venv" "$src/frontend/node_modules"
  cp -R "$src"/. "$ROOT"/
  rm -rf "$tmp"
  BUILD=0
fi
log "installing Python packages"
(cd "$ROOT/backend" && .venv/bin/python -m pip install --quiet -e ".[mcp]")
if command -v node >/dev/null 2>&1 && { [ "$BUILD" = 1 ] || [ ! -f "$ROOT/frontend/dist/index.html" ]; }; then
  log "building the web UI"
  (cd "$ROOT/frontend" && npm ci --no-audit --no-fund && npm run build)
fi
log "restarting"
if systemctl --user is-active --quiet c64-console.service 2>/dev/null; then
  systemctl --user restart c64-console.service
else
  pkill -f "uvicorn app.main:app" || true
  log "start it again with: cd '$ROOT/backend' && .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8064"
fi
log "update finished"
