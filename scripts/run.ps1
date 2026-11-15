# Start AM locally (Windows PowerShell): one process, one address, no internet needed.
#   powershell -ExecutionPolicy Bypass -File scripts\run.ps1 [-Demo] [-Port 8000] [-NoBrowser]
# The API serves the built console, so this only listens on 127.0.0.1 (this computer).
# -Demo turns on test mode: SIMULATED space events (one shortly after start, then every few
# minutes) and the Test Console API (/api/test/*). Use it for demonstrations only.
param([int]$Port = 8000, [switch]$NoBrowser, [switch]$Demo)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path "$root\frontend\dist\index.html")) {
    Write-Host "Console not built yet; building"
    Push-Location "$root\frontend"; npm run build; if ($LASTEXITCODE) { throw "frontend build failed" }; Pop-Location
}
if ($Demo) {
    $env:AM_TEST_MODE = "1"
    Write-Host "Demo mode: simulated space events and the Test Console API are on"
}
$url = "http://localhost:$Port"
Write-Host "AM is starting at $url (Ctrl+C to stop)"
if (-not $NoBrowser) { Start-Job -ScriptBlock { param($u) Start-Sleep 3; Start-Process $u } -ArgumentList $url | Out-Null }
Push-Location "$root\backend"
try { uv run uvicorn am.main:app --host 127.0.0.1 --port $Port }
finally { Pop-Location }
