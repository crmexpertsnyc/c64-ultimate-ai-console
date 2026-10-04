# Windows: start backend (:8064) and frontend dev server (:5173).
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location "$root\backend"
if (-not (Test-Path .venv)) { python -m venv .venv }
& .\.venv\Scripts\python -m pip install -q -e ".[dev,mcp]"
Start-Process -WorkingDirectory "$root\frontend" -FilePath "npm.cmd" -ArgumentList "run","dev"
& .\.venv\Scripts\python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8064
