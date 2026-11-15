# Start AM locally (Windows PowerShell): one process, one address, no internet needed.
#   powershell -ExecutionPolicy Bypass -File scripts\run.ps1 [-Demo] [-Lan] [-Port 8000] [-NoBrowser]
# The API serves the built console. By default it listens on 127.0.0.1 only (this computer).
# -Demo turns on test mode: SIMULATED space events (one shortly after start, then every few
# minutes) and the Test Console API (/api/test/*). Use it for demonstrations only.
# -Lan also lets other computers on the same local network connect (for example the doctor on
# one laptop and the astronaut on another). Only this computer's own addresses are accepted.
param([int]$Port = 8000, [switch]$NoBrowser, [switch]$Demo, [switch]$Lan)
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
$bind = "127.0.0.1"
if ($Lan) {
    $ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } | ForEach-Object { $_.IPAddress })
    if (-not $ips.Count) { throw "No network address found. Connect this computer to the Wi-Fi or a network cable first." }
    $hosts = @("localhost", "127.0.0.1", "[::1]", $env:COMPUTERNAME.ToLower()) + $ips
    $env:AM_ALLOWED_HOSTS = ($hosts | ConvertTo-Json -Compress)
    $bind = "0.0.0.0"
    Write-Host ""
    Write-Host "Network mode. On the other computer (same Wi-Fi), open one of:"
    foreach ($ip in $ips) { Write-Host "    http://${ip}:$Port" }
    Write-Host "If Windows asks whether to allow Python on networks, choose Private networks and Allow."
    Write-Host ""
}
$url = "http://localhost:$Port"
Write-Host "AM is starting at $url (Ctrl+C to stop)"
if (-not $NoBrowser) { Start-Job -ScriptBlock { param($u) Start-Sleep 3; Start-Process $u } -ArgumentList $url | Out-Null }
Push-Location "$root\backend"
try { uv run uvicorn am.main:app --host $bind --port $Port }
finally { Pop-Location }
