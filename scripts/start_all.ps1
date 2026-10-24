<#
  Start everything the dial pad needs, then check it:
    1. the stand-in core API        (port 9000)
    2. the voice service            (port 8080)
    3. the ngrok tunnel             (your permanent ngrok address -> port 8080)
    4. scripts/check_setup.py       (tells you what, if anything, is still wrong)

  Usage (PowerShell, from anywhere):
      .\scripts\start_all.ps1          start
      .\scripts\start_all.ps1 -Stop    stop everything it started
  Logs: apps\voice\.run\*.log
#>
param([switch]$Stop)

$ErrorActionPreference = "Stop"
$root   = Split-Path -Parent $PSScriptRoot
$voice  = Join-Path $root "apps\voice"
$run    = Join-Path $voice ".run"
$py     = Join-Path $voice ".venv\Scripts\python.exe"
$ngrok  = (Get-Command ngrok -ErrorAction SilentlyContinue).Source
if (-not $ngrok) { $ngrok = Join-Path $env:LOCALAPPDATA "ngrok\ngrok.exe" }
$pidFile = Join-Path $run "pids.txt"

function Stop-Ours {
    if (Test-Path $pidFile) {
        Get-Content $pidFile | ForEach-Object {
            if ($_ -match '^\d+$') { Stop-Process -Id ([int]$_) -Force -ErrorAction SilentlyContinue }
        }
        Remove-Item $pidFile -ErrorAction SilentlyContinue
    }
    # uvicorn on Windows leaves a child process holding the port
    foreach ($port in 8080, 9000) {
        Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
            ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    }
    Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}

if ($Stop) { Stop-Ours; Write-Host "Stopped."; return }

if (-not (Test-Path $py))    { throw "Python environment not found: $py`nCreate it first (README, section 5)." }
if (-not (Test-Path $ngrok)) { throw "ngrok not found. Install it from ngrok.com and run: ngrok config add-authtoken <your token>" }
if (-not (Test-Path (Join-Path $voice ".env"))) { throw "apps\voice\.env is missing. Copy .env.example to .env and fill it in." }

New-Item -ItemType Directory -Force $run | Out-Null
Stop-Ours
New-Item -ItemType File -Force $pidFile | Out-Null

function Start-Bg($name, $file, $argList) {
    $p = Start-Process -FilePath $file -ArgumentList $argList -WorkingDirectory $voice -WindowStyle Hidden -PassThru `
         -RedirectStandardOutput (Join-Path $run "$name.log") -RedirectStandardError (Join-Path $run "$name.err.log")
    Add-Content $pidFile $p.Id
    return $p
}

# the public address comes from .env so there is one place to change it
$publicUrl = (Select-String -Path (Join-Path $voice ".env") -Pattern '^PUBLIC_VOICE_URL=(.+)$').Matches.Groups[1].Value.Trim()
$domain = ([Uri]$publicUrl).Host

Write-Host "Starting core stand-in (9000)..."
Start-Bg "core"  $py @("-m", "uvicorn", "mock_core.app:app", "--port", "9000") | Out-Null
Write-Host "Starting voice service (8080)..."
Start-Bg "voice" $py @("-m", "uvicorn", "app.main:app", "--port", "8080") | Out-Null
Write-Host "Starting ngrok tunnel ($domain -> 8080)..."
Start-Bg "ngrok" $ngrok @("http", "8080", "--url=$domain", "--log=$(Join-Path $run 'ngrok-agent.log')") | Out-Null

# wait until the service answers through the tunnel
$deadline = (Get-Date).AddSeconds(40)
$up = $false
while ((Get-Date) -lt $deadline -and -not $up) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-RestMethod "$publicUrl/health" -Headers @{ "ngrok-skip-browser-warning" = "true" } -TimeoutSec 4
        $up = ($r.service -eq "acp-voice")
    } catch { }
}
if (-not $up) { Write-Warning "Not reachable through $publicUrl yet. See apps\voice\.run\*.log" }

Push-Location $voice
& $py "scripts\check_setup.py"
Pop-Location

Write-Host ""
Write-Host "Backend address : $publicUrl"
Write-Host "Dial pad page   : https://gojosatorou999.github.io/aicte-call/"
Write-Host "Stop everything : .\scripts\start_all.ps1 -Stop"
