# Batch A: evaluation runs on the submitted build, using the repository's own runner and scripts.
# Every live source (Google Places, SerpAPI, YouTube, Wikipedia, DeepSeek Search) is blocked by
# offline.py; evidence comes from the curated sandbox corpus and only model calls go out.
# Run in PowerShell:  & "C:\Users\Dies Irae\Desktop\HKU\LLM\Final Project\Deliverable\tests\run_batch_a.ps1"
# Results (one SQLite file and one log per run) go to Final Project\Deliverable\test_results.

$repo = "C:\Users\Dies Irae\Desktop\HKU\LLM\Pathfinder v5"
$tests = "C:\Users\Dies Irae\Desktop\HKU\LLM\Final Project\Deliverable\tests"
$out = "C:\Users\Dies Irae\Desktop\HKU\LLM\Final Project\Deliverable\test_results"
New-Item -ItemType Directory -Force $out | Out-Null

# Some component scripts overwrite their *-latest files in docs/eval-artifacts.
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
Copy-Item "$repo\docs\eval-artifacts" "$out\eval-artifacts-backup-$stamp" -Recurse

Set-Location "$repo\backend"
$py = "$repo\backend\.venv\Scripts\python.exe"
$offline = "$tests\offline.py"
$env:PYTHONPATH = "."
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
$env:EVAL_CONCURRENCY = "2"
# Shipped defaults from .env.example: Sonnet 5.5 for every writer part, DeepSeek Flash verifier,
# two repair rounds. The local .env moves edit/ask/search to DeepSeek and uses one repair round;
# an empty map would be merged with .env, so the three parts are named explicitly.
$env:PURPOSE_MODELS = '{"edit":"claude-sonnet-5.5","ask":"claude-sonnet-5.5","search_query":"claude-sonnet-5.5"}'
$env:MAX_REPAIRS = "2"
"commit $(git -C $repo rev-parse --short HEAD)  started $(Get-Date -Format s)" | Out-File "$out\commit.txt"

function Run-Suite([string]$name, [string[]]$suites) {
    $env:DATABASE_PATH = "$out\$name.db"
    $env:EVAL_TAG = $name
    Write-Host "=== $name : $($suites -join ', ') ===" -ForegroundColor Cyan
    & $py $offline eval @suites 2>&1 | Tee-Object -FilePath "$out\$name.log"
}

function Run-Script([string]$name, [string]$script, [string]$artifact) {
    Write-Host "=== $name : $script ===" -ForegroundColor Cyan
    & $py $offline script "scripts\$script" 2>&1 | Tee-Object -FilePath "$out\$name.log"
    if ($artifact -and (Test-Path "$repo\docs\eval-artifacts\v5-model-study\$artifact")) {
        Copy-Item "$repo\docs\eval-artifacts\v5-model-study\$artifact" "$out\$name.json"
    }
}

# 1. Plan suites, three repeats: Hong Kong disruptions (8), Japan constraints (6), TravelPlanner-style (3).
foreach ($r in 1..3) {
    Run-Suite "hk_r$r" @("hongkong")
    Run-Suite "jp_tp_r$r" @("japan", "travelplanner")
}

# 2. Routing on the 84 frozen utterances, three repeats (run-to-run variance; confidence calibration).
foreach ($r in 1..3) { Run-Suite "routing_r$r" @("routing") }

# 3. Direct injection: all 36 probes typed by the user (4 attack types; includes held-out probes).
$env:EVAL_CASES = "i01,i02,i03,i04,i05,i06,i07,i08,i09,i10,i11,i12,i13,i14,i15,i16,i17,i18,i19,i20,i21,i22,i23,i24,i25,i26,i27,i28,i29,i30,i31,i32,i33,i34,i35,i36"
Run-Suite "injection_direct" @("injection")
Remove-Item Env:EVAL_CASES

# 4. Path cost per message type (10 mixed messages) and the edit suite (5 edits on a seeded plan).
Run-Suite "efficiency" @("efficiency")
Run-Suite "edit" @("edit_vs_replan")

# 5. Repair-loop ablation: the same plan suites with no repair round.
$env:MAX_REPAIRS = "0"
Run-Suite "norepair" @("hongkong", "japan", "travelplanner")
$env:MAX_REPAIRS = "2"

# 6. Component re-runs: shipped verifier twice (mutated plans with human gold labels),
#    full skill vs minimal prompt for the analyst, editor at temperature 0 and 0.2.
$env:DATABASE_PATH = "$out\components.db"
Run-Script "verifier_r1" "v5_verifier_current.py" "verifier-current.json"
Run-Script "verifier_r2" "v5_verifier_current.py" "verifier-current.json"
$env:SKILL_MODEL = "claude-sonnet-5.5"
Run-Script "skills_ablation" "v5_skills_ablation.py" "skills-ablation-latest.json"
Run-Script "editor_temperature" "v5_editor_temperature.py" "editor-temperature-latest.json"

"finished $(Get-Date -Format s)" | Out-File "$out\commit.txt" -Append
Write-Host "Batch A finished. Results are in $out" -ForegroundColor Green
