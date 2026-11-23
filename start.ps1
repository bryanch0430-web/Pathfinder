# Pathfinder v5 — start the API and the built UI on http://127.0.0.1:8101
#
#   .\start.ps1              # uses backend\.venv
#   .\start.ps1 -Port 8102   # another port
#   .\start.ps1 -Sandbox     # curated offline evidence (no provider calls)
#
# The React UI is served from frontend\dist by the same process.

param(
    [int]$Port = 8101,
    [switch]$Sandbox,
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

$python = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $python = "python"
    Write-Host "backend\.venv not found; using the python on PATH." -ForegroundColor Yellow
}

if (-not (Test-Path (Join-Path $root ".env"))) {
    Write-Host "No .env found. Copy .env.example to .env and add a writer key." -ForegroundColor Yellow
}

$env:PYTHONPATH = Join-Path $root "backend"
if ($Sandbox) { $env:PATHFINDER_SANDBOX = "1" }

$ui = Join-Path $root "frontend\dist\index.html"
if (-not (Test-Path $ui)) {
    Write-Host "frontend\dist is missing: build the UI first with  cd frontend; npm install; npm run build" -ForegroundColor Yellow
}

$uvicornArgs = @("-m", "uvicorn", "app.main:app", "--app-dir", (Join-Path $root "backend"), "--host", "127.0.0.1", "--port", "$Port")
if ($Reload) { $uvicornArgs += "--reload" }

Write-Host ""
Write-Host "  Pathfinder v5"
Write-Host "  UI + API : http://127.0.0.1:$Port"
Write-Host "  API docs : http://127.0.0.1:$Port/docs"
Write-Host "  sandbox  : $($Sandbox.IsPresent)"
Write-Host ""

& $python @uvicornArgs
