# Start AM locally (Windows PowerShell): one process, one address, no internet needed.
#   powershell -ExecutionPolicy Bypass -File scripts\run.ps1 [-Port 8000] [-NoBrowser]
# The API serves the built console, so this only listens on 127.0.0.1 (this computer).
param([int]$Port = 8000, [switch]$NoBrowser)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path "$root\frontend\dist\index.html")) {
    Write-Host "Console not built yet; building"
    Push-Location "$root\frontend"; npm run build; if ($LASTEXITCODE) { throw "frontend build failed" }; Pop-Location
}
$url = "http://localhost:$Port"
Write-Host "AM is starting at $url (Ctrl+C to stop)"
if (-not $NoBrowser) { Start-Job -ScriptBlock { param($u) Start-Sleep 3; Start-Process $u } -ArgumentList $url | Out-Null }
Push-Location "$root\backend"
try { uv run uvicorn am.main:app --host 127.0.0.1 --port $Port }
finally { Pop-Location }
