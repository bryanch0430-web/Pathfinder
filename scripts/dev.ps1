# Pathfinder 30 development helpers
#
#   .\scripts\dev.ps1 backend
#   .\scripts\dev.ps1 frontend
#   .\scripts\dev.ps1 storybook
#   .\scripts\dev.ps1 test
#   .\scripts\dev.ps1 eval hongkong baseline-v1
#
# The backend, its tests and the evaluation run in backend\.venv, never in whichever python
# is first on PATH. Create it once:
#   python -m venv backend/.venv && backend/.venv/Scripts/pip install -r backend/requirements.txt

param(
  [Parameter(Position = 0)]
  [ValidateSet("backend", "frontend", "storybook", "test", "eval")]
  [string]$Task = "backend",
  [Parameter(Position = 1)]
  [string]$Dataset = "hongkong",
  [Parameter(Position = 2)]
  [string]$Tag = "baseline-v1"
)

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Py = Join-Path $Root "backend\.venv\Scripts\python.exe"

# The backend treats an unset ENVIRONMENT as production and refuses the built-in secrets.
if (-not $env:ENVIRONMENT) { $env:ENVIRONMENT = "development" }

function Assert-Venv {
  if (-not (Test-Path $Py)) {
    Write-Host "Create the venv first: python -m venv backend/.venv && backend/.venv/Scripts/pip install -r backend/requirements.txt"
    exit 1
  }
}

switch ($Task) {
  "backend" {
    Assert-Venv
    $env:PYTHONPATH = "backend"
    & $Py -m uvicorn app.main:app --port 8100 --host 127.0.0.1
  }
  "frontend" {
    Set-Location "$Root\frontend"
    npm run dev
  }
  "storybook" {
    Set-Location "$Root\frontend"
    npm run storybook
  }
  "test" {
    Assert-Venv
    Set-Location "$Root\backend"
    $env:PYTHONPATH = "."
    & $Py -m pytest
  }
  "eval" {
    Assert-Venv
    Set-Location "$Root\backend"
    $env:PYTHONPATH = "."
    & $Py -m app.eval_run --dataset $Dataset --tag $Tag --out "$Root\data\eval"
  }
}
