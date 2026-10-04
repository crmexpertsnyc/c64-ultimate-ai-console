# C64 Ultimate AI Console - one-command install for Windows 10/11.
#
#   From a copy of the project:   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#   Straight from GitHub:         irm https://raw.githubusercontent.com/<owner>/<repo>/main/scripts/install.ps1 | iex
#
# What it does (nothing else):
#   1. finds Python 3.11+ (and Node 20+ only if the web UI still has to be built)
#   2. without a project copy: downloads the latest release into %LOCALAPPDATA%\C64Console (a short path - very
#      long paths break some Python packages on Windows)
#   3. creates backend\.venv and installs the console into it
#   4. builds the web UI if the release doesn't include it
#   5. starts the console at sign-in (a scheduled task with a restart watchdog) - skip with -NoAutostart
#   6. starts it now and opens http://localhost:8064
# Your settings and library database live in backend\data and are never touched by install or update.

param(
    [string]$Repo = $(if ($env:C64_CONSOLE_REPO) { $env:C64_CONSOLE_REPO } else { '' }),   # owner/name on GitHub
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA 'C64Console'),
    [switch]$NoAutostart,
    [switch]$NoBrowser,
    [switch]$NoStart          # install only (used by tests)
)
$ErrorActionPreference = 'Stop'

function Say($msg) { Write-Host "  $msg" }
function Step($msg) { Write-Host ""; Write-Host "== $msg" -ForegroundColor Cyan }
function Fail($msg) { Write-Host ""; Write-Host "!! $msg" -ForegroundColor Red; exit 1 }

function Find-Python {
    foreach ($cmd in @(@('py', '-3'), @('python'), @('python3'))) {
        try {
            $exe = $cmd[0]; $args0 = @($cmd | Select-Object -Skip 1)
            $v = & $exe @args0 -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($LASTEXITCODE -eq 0 -and $v) {
                $parts = $v.Trim().Split('.')
                if ([int]$parts[0] -eq 3 -and [int]$parts[1] -ge 11) { return ,$cmd }
            }
        } catch { }
    }
    return $null
}

Write-Host ""
Write-Host "  C64 Ultimate AI Console - installer" -ForegroundColor Magenta

# ---------------------------------------------------------------- where is the project?
$root = $null
if ($PSScriptRoot -and (Test-Path (Join-Path (Split-Path -Parent $PSScriptRoot) 'backend\pyproject.toml'))) {
    $root = Split-Path -Parent $PSScriptRoot
} elseif (Test-Path (Join-Path $InstallDir 'backend\pyproject.toml')) {
    $root = $InstallDir
} else {
    Step "Downloading the latest release"
    if (-not $Repo) { Fail "Tell me which GitHub project to install: -Repo owner/name (or set C64_CONSOLE_REPO)." }
    $rel = Invoke-RestMethod -UseBasicParsing "https://api.github.com/repos/$Repo/releases/latest" -Headers @{ 'User-Agent' = 'c64-console-installer' }
    $zipUrl = ($rel.assets | Where-Object { $_.name -like '*.zip' } | Select-Object -First 1).browser_download_url
    if (-not $zipUrl) { $zipUrl = $rel.zipball_url }
    $zip = Join-Path $env:TEMP 'c64console-release.zip'
    Say "$($rel.tag_name) from $zipUrl"
    Invoke-WebRequest -UseBasicParsing $zipUrl -OutFile $zip
    $tmp = Join-Path $env:TEMP ('c64console-' + [guid]::NewGuid().ToString('N'))
    Expand-Archive -Path $zip -DestinationPath $tmp
    $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    Copy-Item -Recurse -Force (Join-Path $inner.FullName '*') $InstallDir
    Remove-Item -Recurse -Force $tmp, $zip
    $root = $InstallDir
}
if ($root.Length -gt 120) {
    Write-Host "  Note: the project path is long ($($root.Length) characters). If Python packages fail to load, move it to a short path such as C:\c64console." -ForegroundColor Yellow
}
Say "Project: $root"
$backend = Join-Path $root 'backend'
$frontend = Join-Path $root 'frontend'

# ---------------------------------------------------------------- Python
Step "Checking Python"
$py = Find-Python
if (-not $py) {
    Fail "Python 3.11 or newer is needed. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), or run: winget install Python.Python.3.12"
}
$pyExe = $py[0]; $pyArgs = @($py | Select-Object -Skip 1)
Say ("Using " + (& $pyExe @pyArgs --version))

Step "Installing the console (this takes a few minutes the first time)"
$venvPy = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) { & $pyExe @pyArgs -m venv (Join-Path $backend '.venv') }
& $venvPy -m pip install --quiet --upgrade pip
Push-Location $backend
try { & $venvPy -m pip install --quiet -e ".[mcp]" } finally { Pop-Location }
if ($LASTEXITCODE -ne 0) { Fail "Installing the Python packages failed (see the messages above)." }

# ---------------------------------------------------------------- web UI
if (Test-Path (Join-Path $frontend 'dist\index.html')) {
    Step "Web UI: already built"
} else {
    Step "Building the web UI"
    $node = Get-Command node -ErrorAction SilentlyContinue
    if (-not $node) { Fail "Node.js 20+ is needed to build the web UI (release downloads include it pre-built). Install it: winget install OpenJS.NodeJS.LTS" }
    $nodeMajor = [int]((& node --version).TrimStart('v').Split('.')[0])
    if ($nodeMajor -lt 20) { Fail "Node.js 20 or newer is needed (found $(& node --version))." }
    Push-Location $frontend
    try { & npm ci --no-audit --no-fund; if ($LASTEXITCODE -eq 0) { & npm run build } } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { Fail "Building the web UI failed (see the messages above)." }
}

# ---------------------------------------------------------------- start
if ($NoStart) { Write-Host ''; Write-Host '  Installed (not started: -NoStart).' -ForegroundColor Green; exit 0 }
if (-not $NoAutostart) {
    Step "Starting the console automatically at sign-in"
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'scripts\install-autostart.ps1')
    Start-ScheduledTask -TaskName 'C64 AI Console'
} else {
    Step "Starting the console"
    Start-Process powershell -WindowStyle Hidden -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $root 'scripts\run-console.ps1')
}

Say "Waiting for it to come up..."
$ok = $false
foreach ($i in 1..40) {
    try { if ((Invoke-RestMethod -UseBasicParsing 'http://127.0.0.1:8064/api/health' -TimeoutSec 2).ok) { $ok = $true; break } } catch { }
    Start-Sleep -Seconds 2
}
if (-not $ok) { Fail "The console didn't start. Look at $backend\data\logs\console.log" }

Write-Host ""
Write-Host "  Done! Open http://localhost:8064 - the setup wizard connects your C64 Ultimate." -ForegroundColor Green
Write-Host "  Other devices on your network: http://$((Get-NetIPAddress -AddressFamily IPv4 -PrefixOrigin Dhcp -ErrorAction SilentlyContinue | Select-Object -First 1).IPAddress):8064"
Write-Host "  Update later with: powershell -ExecutionPolicy Bypass -File `"$root\scripts\update.ps1`""
if (-not $NoBrowser) { Start-Process 'http://localhost:8064' }
