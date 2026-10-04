# Update the C64 Ultimate AI Console to the latest version (Windows).
#   powershell -ExecutionPolicy Bypass -File scripts\update.ps1
# Also started by Settings -> About & updates -> "Update now" (on the console's own computer).
#
# A git checkout is updated with `git pull --ff-only`; otherwise the latest GitHub release is downloaded.
# Never touched: backend\data (settings, library database, saves, backups) and your virtual environment's
# location. Afterwards the console restarts by itself (its watchdog brings it back within ~10 s).

param([string]$Repo = $(if ($env:C64_CONSOLE_REPO) { $env:C64_CONSOLE_REPO } else { '' }))
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'
$logDir = Join-Path $backend 'data\logs'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir 'update.log'
function Log($msg) { $line = "[$(Get-Date -Format s)] $msg"; Write-Host $line; Add-Content -Path $log -Value $line -Encoding utf8 }

Log "update started in $root"
if (-not $Repo) {
    try { $Repo = (Get-Content (Join-Path $backend 'data\settings.json') -Raw | ConvertFrom-Json).UPDATE_REPO } catch { }
}

$git = Get-Command git -ErrorAction SilentlyContinue
$isGit = $git -and (Test-Path (Join-Path $root '.git'))
if ($isGit) {
    Log "git checkout: pulling"
    Push-Location $root
    try {
        $dirty = (& git status --porcelain --untracked-files=no)
        if ($dirty) { throw "You have local changes in the project - commit or stash them, then update again." }
        & git pull --ff-only
        if ($LASTEXITCODE -ne 0) { throw "git pull failed (see above)." }
    } finally { Pop-Location }
} else {
    if (-not $Repo) { throw "No update source: set UPDATE_REPO in Settings (owner/name on GitHub) or pass -Repo." }
    Log "downloading the latest release of $Repo"
    $rel = Invoke-RestMethod -UseBasicParsing "https://api.github.com/repos/$Repo/releases/latest" -Headers @{ 'User-Agent' = 'c64-console-updater' }
    $zipUrl = ($rel.assets | Where-Object { $_.name -like '*.zip' } | Select-Object -First 1).browser_download_url
    if (-not $zipUrl) { $zipUrl = $rel.zipball_url }
    $zip = Join-Path $env:TEMP 'c64console-update.zip'
    Invoke-WebRequest -UseBasicParsing $zipUrl -OutFile $zip
    $tmp = Join-Path $env:TEMP ('c64console-' + [guid]::NewGuid().ToString('N'))
    Expand-Archive -Path $zip -DestinationPath $tmp
    $inner = (Get-ChildItem $tmp -Directory | Select-Object -First 1).FullName
    # copy everything except your data, virtual environment and node_modules
    & robocopy $inner $root /E /NFL /NDL /NJH /NJS /NP /XD (Join-Path $inner 'backend\data') (Join-Path $inner 'backend\.venv') (Join-Path $inner 'frontend\node_modules') | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "Copying the new version failed (robocopy $LASTEXITCODE)." }
    Remove-Item -Recurse -Force $tmp, $zip
    Log "now at $($rel.tag_name)"
}

Log "installing Python packages"
$venvPy = Join-Path $backend '.venv\Scripts\python.exe'
Push-Location $backend
try { & $venvPy -m pip install --quiet -e ".[mcp]" } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { throw "Installing Python packages failed." }

$node = Get-Command node -ErrorAction SilentlyContinue
if ($node -and ($isGit -or -not (Test-Path (Join-Path $frontend 'dist\index.html')))) {
    Log "building the web UI"
    Push-Location $frontend
    try { & npm ci --no-audit --no-fund; if ($LASTEXITCODE -eq 0) { & npm run build } } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "Building the web UI failed." }
} elseif (-not (Test-Path (Join-Path $frontend 'dist\index.html'))) {
    throw "The web UI needs building but Node.js isn't installed (winget install OpenJS.NodeJS.LTS)."
}

Log "restarting the console"
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*uvicorn app.main:app*' }
if ($procs) {
    $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }      # the watchdog starts it again
} else {
    try { Start-ScheduledTask -TaskName 'C64 AI Console' } catch { Log "start it with scripts\run-console.ps1" }
}
Log "update finished"
