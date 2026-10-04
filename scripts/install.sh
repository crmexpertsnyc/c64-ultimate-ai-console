#!/usr/bin/env bash
# C64 Ultimate AI Console — one-command install for Linux and macOS.
#
#   From a copy of the project:  bash scripts/install.sh
#   Straight from GitHub:        curl -fsSL https://raw.githubusercontent.com/<owner>/<repo>/main/scripts/install.sh | bash -s -- --repo <owner>/<repo>
#
# Finds Python 3.11+ (Node 20+ only if the web UI must be built), downloads the latest release into
# ~/c64console when run outside a project copy, installs into backend/.venv, and (Linux with systemd) starts the
# console at login as a user service. Options: --repo owner/name  --dir PATH  --no-service
# Your data (backend/data) is never touched by install or update.
set -euo pipefail

REPO="${C64_CONSOLE_REPO:-}"
DIR="$HOME/c64console"
SERVICE=1
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO="$2"; shift 2 ;;
    --dir) DIR="$2"; shift 2 ;;
    --no-service) SERVICE=0; shift ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
done

say()  { printf '  %s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
fail() { printf '\n!! %s\n' "$*" >&2; exit 1; }

echo; echo "  C64 Ultimate AI Console — installer"

here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$here" ] && [ -f "$here/../backend/pyproject.toml" ]; then
  ROOT="$(cd "$here/.." && pwd)"
elif [ -f "$DIR/backend/pyproject.toml" ]; then
  ROOT="$DIR"
else
  step "Downloading the latest release"
  [ -n "$REPO" ] || fail "Tell me which GitHub project to install: --repo owner/name"
  api="$(curl -fsSL -H 'User-Agent: c64-console-installer' "https://api.github.com/repos/$REPO/releases/latest")"
  url="$(printf '%s' "$api" | python3 -c 'import json,sys;d=json.load(sys.stdin);a=[x["browser_download_url"] for x in d.get("assets",[]) if x["name"].endswith((".tar.gz",".zip"))];print(a[0] if a else d["tarball_url"])')"
  tmp="$(mktemp -d)"
  say "$url"
  curl -fsSL "$url" -o "$tmp/release"
  mkdir -p "$tmp/x" "$DIR"
  if unzip -tq "$tmp/release" >/dev/null 2>&1; then unzip -q "$tmp/release" -d "$tmp/x"; else tar -xzf "$tmp/release" -C "$tmp/x"; fi
  cp -R "$(find "$tmp/x" -mindepth 1 -maxdepth 1 -type d | head -n1)"/. "$DIR"/
  rm -rf "$tmp"
  ROOT="$DIR"
fi
say "Project: $ROOT"

step "Checking Python"
PY=""
for c in python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then PY="$c"; break; fi
done
[ -n "$PY" ] || fail "Python 3.11 or newer is needed (e.g. sudo apt install python3.12 python3.12-venv, or brew install python@3.12)."
say "Using $("$PY" --version)"

step "Installing the console (a few minutes the first time)"
[ -x "$ROOT/backend/.venv/bin/python" ] || "$PY" -m venv "$ROOT/backend/.venv"
"$ROOT/backend/.venv/bin/python" -m pip install --quiet --upgrade pip
(cd "$ROOT/backend" && .venv/bin/python -m pip install --quiet -e ".[mcp]")

if [ -f "$ROOT/frontend/dist/index.html" ]; then
  step "Web UI: already built"
else
  step "Building the web UI"
  command -v node >/dev/null 2>&1 || fail "Node.js 20+ is needed to build the web UI (release downloads include it pre-built)."
  [ "$(node -p 'process.versions.node.split(".")[0]')" -ge 20 ] || fail "Node.js 20 or newer is needed."
  (cd "$ROOT/frontend" && npm ci --no-audit --no-fund && npm run build)
fi

RUN="cd '$ROOT/backend' && exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8064"
if [ "$SERVICE" = 1 ] && command -v systemctl >/dev/null 2>&1 && [ "$(uname)" = "Linux" ]; then
  step "Starting the console at login (systemd user service)"
  mkdir -p "$HOME/.config/systemd/user"
  cat > "$HOME/.config/systemd/user/c64-console.service" <<EOF
[Unit]
Description=C64 Ultimate AI Console
After=network-online.target

[Service]
ExecStart=/bin/sh -c "$RUN"
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now c64-console.service
  say "Tip: 'loginctl enable-linger $USER' keeps it running when you're logged out."
else
  step "Starting the console"
  mkdir -p "$ROOT/backend/data/logs"
  nohup sh -c "$RUN" >> "$ROOT/backend/data/logs/console.log" 2>&1 &
  say "(No service installed — start it again later with: sh -c \"$RUN\")"
fi

say "Waiting for it to come up..."
for _ in $(seq 1 40); do
  if curl -fs http://127.0.0.1:8064/api/health >/dev/null 2>&1; then
    echo; echo "  Done! Open http://localhost:8064 — the setup wizard connects your C64 Ultimate."
    echo "  Update later with: bash '$ROOT/scripts/update.sh'"
    exit 0
  fi
  sleep 2
done
fail "The console didn't start — see $ROOT/backend/data/logs/console.log (or: journalctl --user -u c64-console)"
