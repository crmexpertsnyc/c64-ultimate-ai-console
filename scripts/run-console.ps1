# Runs the C64 Ultimate AI Console in the background and restarts it if it stops.
# Started at logon by the "C64 AI Console" scheduled task (see install-autostart.ps1).
# Output goes to backend\data\logs\console.log (the previous run is kept as console.old.log).

$ErrorActionPreference = 'Continue'
$backend = Join-Path (Split-Path -Parent $PSScriptRoot) 'backend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
$logs = Join-Path $backend 'data\logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$log = Join-Path $logs 'console.log'

Set-Location $backend
while ($true) {
    if (Test-Path $log) { Move-Item -Force $log (Join-Path $logs 'console.old.log') }
    "[$(Get-Date -Format s)] starting console on port 8064" | Out-File -FilePath $log -Encoding utf8
    # cmd handles the redirection so the log is plain text and never blocks the server.
    & cmd.exe /c "`"$python`" -m uvicorn app.main:app --host 0.0.0.0 --port 8064 >> `"$log`" 2>&1"
    "[$(Get-Date -Format s)] console stopped (exit $LASTEXITCODE); restarting in 10 s" | Out-File -FilePath $log -Append -Encoding utf8
    Start-Sleep -Seconds 10
}
