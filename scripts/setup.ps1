# AM one-command setup (Windows PowerShell).
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 [-Demo] [-PullModels]
# Installs dependencies, builds the console, prepares the local database and the knowledge
# base. -Demo adds the SIMULATED demo crew. -PullModels downloads the local AI models
# (about 5 GB) with Ollama.
param([switch]$Demo, [switch]$PullModels)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Need($cmd, $hint) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "Missing '$cmd'. $hint" }
}
Need "uv" "Install uv: https://docs.astral.sh/uv/"
Need "npm" "Install Node.js 20 or newer: https://nodejs.org/"

Write-Host "1/5 Backend dependencies"
Push-Location "$root\backend"; uv sync; if ($LASTEXITCODE) { throw "uv sync failed" }; Pop-Location

Write-Host "2/5 Console dependencies and build"
Push-Location "$root\frontend"
npm ci; if ($LASTEXITCODE) { throw "npm ci failed" }
npm run build; if ($LASTEXITCODE) { throw "frontend build failed" }
Pop-Location

if ($PullModels) {
    Need "ollama" "Install Ollama: https://ollama.com/ (optional; AM works without it)"
    Write-Host "Pulling local models (about 5 GB)"
    ollama pull qwen2.5:7b-instruct
    ollama pull nomic-embed-text
}

Push-Location "$root\backend"
Write-Host "3/5 Database"
uv run python -m am.cli migrate; if ($LASTEXITCODE) { throw "migration failed" }
Write-Host "4/5 Knowledge base"
uv run python -m am.cli ingest-knowledge
if ($Demo) {
    Write-Host "5/5 Demo crew (SIMULATED data)"
    uv run python -m am.cli seed-demo; if ($LASTEXITCODE) { throw "demo seed failed" }
} else {
    Write-Host "5/5 Skipped demo crew (add -Demo to create it)"
}
uv run python -m am.cli status
Pop-Location
Write-Host "Done. Start AM with: powershell -ExecutionPolicy Bypass -File scripts\run.ps1"
