# Runs every evaluation for the report. Batch A opens in its own window; Batches B and C run here, then the
# analysis writes test_results\summary.json. Live sources are blocked; only model calls go out.
# Start with:  powershell -ExecutionPolicy Bypass -File "C:\Users\Dies Irae\Desktop\HKU\LLM\Final Project\Deliverable\tests\run_all.ps1"

$tests = "C:\Users\Dies Irae\Desktop\HKU\LLM\Final Project\Deliverable\tests"
$out = "C:\Users\Dies Irae\Desktop\HKU\LLM\Final Project\Deliverable\test_results"
$py = "C:\Users\Dies Irae\Desktop\HKU\LLM\Pathfinder v5\backend\.venv\Scripts\python.exe"
New-Item -ItemType Directory -Force $out | Out-Null

Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-File", "`"$tests\run_batch_a.ps1`""

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
& $py "$tests\batch_b.py" indirect paired 2>&1 | Tee-Object -FilePath "$out\batch_b.log"
& $py "$tests\batch_c.py" baseline boundary travelplanner jev 2>&1 | Tee-Object -FilePath "$out\batch_c.log"

Write-Host "Batches B and C finished. When the Batch A window says it has finished, run:" -ForegroundColor Green
Write-Host "  & `"$py`" `"$tests\analyze_results.py`"" -ForegroundColor Green
