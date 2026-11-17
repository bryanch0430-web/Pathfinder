# Start Pathfinder locally on http://127.0.0.1:8100
#
#   .\start.ps1              run as installed (serves the built frontend)
#   .\start.ps1 -Rebuild     rebuild the frontend first
#   .\start.ps1 -Port 8110   listen on another port
#
# Reads .env at the repository root. Stop with Ctrl+C, or see DEPLOY-LOCAL.md.

param(
  [int]$Port = 8100,
  [switch]$Rebuild
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Py = Join-Path $Root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
  Write-Host "Creating backend\.venv and installing the backend ..."
  py -3.13 -m venv backend/.venv
  & $Py -m pip install --no-cache-dir -r backend\requirements.txt
}

if ($Rebuild -or -not (Test-Path (Join-Path $Root "frontend\dist\index.html"))) {
  Write-Host "Building the frontend ..."
  Push-Location frontend
  npm install --no-audit --no-fund
  npm run build
  Pop-Location
}

if (-not (Test-Path (Join-Path $Root ".env"))) {
  Write-Warning "No .env at $Root. The app will start, but every source reports unavailable."
}

$env:PYTHONPATH = "backend"
Write-Host ""
Write-Host "  Pathfinder   http://127.0.0.1:$Port" -ForegroundColor Green
Write-Host "  API docs     http://127.0.0.1:$Port/docs"
Write-Host "  Readiness    http://127.0.0.1:$Port/api/ready"
Write-Host ""
& $Py -m uvicorn app.main:app --host 127.0.0.1 --port $Port
