# Pathfinder 30 development helpers
#
#   .\scripts\dev.ps1 backend
#   .\scripts\dev.ps1 frontend
#   .\scripts\dev.ps1 storybook
#   .\scripts\dev.ps1 test
#   .\scripts\dev.ps1 eval hongkong baseline-v1

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

switch ($Task) {
  "backend" {
    $env:PYTHONPATH = "backend"
    python -m uvicorn app.main:app --port 8100 --host 127.0.0.1
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
    Set-Location "$Root\backend"
    $env:PYTHONPATH = "."
    python -m pytest
  }
  "eval" {
    Set-Location "$Root\backend"
    $env:PYTHONPATH = "."
    python -m app.eval_run --dataset $Dataset --tag $Tag --out "$Root\data\eval"
  }
}
