# Code and checks

**Submitted version:** v5.2.11, annotated tag `submission-v5.2.11` (branch
`pathfinder_v5`). `git show submission-v5.2.11` identifies the exact commit.

This is the single index the submission requirement asks for: source code,
prompts/configuration, **one ordinary task**, **one changed request**, **one
failure case**, evaluation scripts/results and success/failure traces.

## Source code and configuration

| Item | Location |
|---|---|
| Runnable entry point | `backend/app/main.py` → `app.main:app`; run command in the root [README](../../README.md#quick-start) |
| Backend source | `backend/app/` (`api/`, `turn/`, `plan/`, `understand/`, `evidence/`, `eval/`, `security/`) |
| Frontend source | `frontend/src/` (React, Vite, TypeScript) |
| Prompts / skills | `backend/app/skills/*.md`, `backend/app/core/prompts.py` |
| Configuration | `backend/app/config.py`, `.env.example`; versions in `backend/requirements.txt`, `frontend/package.json` |
| Tests | `backend/tests/`; commands in the root README |

## The one ordinary task

**Input:** [`ordinary-input.txt`](ordinary-input.txt)

```text
Plan a 2-day Taipei trip from Hong Kong for 1 person starting 4 December 2026, budget 12,000 TWD.
```

**Expected:** a normal plan turn — understand, gather evidence, compose, validate,
save the plan.

**Observed on the submitted build:** one plan generation, about **27 s** total
(model load varies); a sourced two-day plan is returned. The historical exported
turn is `traces/success-taipei-turn-log.json`.

## The one changed request

**Input:** [`changed-input.txt`](changed-input.txt), sent after the ordinary plan
exists:

```text
Remove all attractions from Day 2.
```

**Expected:** an **edit proposal**, not a silent change. The editor returns a typed
proposal that deletes the Day 2 attraction stops; the traveller is asked to
**accept or reject the deletion**, and nothing changes until the tick is accepted.
Meals, the hotel and the rest of the plan stay unless the proposal says otherwise;
an ambiguous “attractions” should trigger a question before deleting.

**Evidence:** no trace of this exact request is recorded. The recorded editor
evidence is `docs/eval-artifacts/v5-model-study/editor-direct-latest.json`: 8 single-stop edits (for example `ed05`, “Remove Nishiki Market,
keep everything else.”) on 3 models, 24/24 passed. A whole-day bulk delete is not
in that set, so the expected behaviour above is design intent, not a measured result.

## The one failure case

**Input:** [`failure-input.txt`](failure-input.txt)

```text
Plan a 4-day Kyoto trip from Hong Kong for 2 people starting 20 April 2027, ending 18 April 2027.
```

**Expected:** the contradictory date range is clarified; nothing is silently
planned.

**What enforces it:** only the analyst model. `finish_dates` in
`backend/app/core/dates.py` swaps a reversed range instead of rejecting it, so the
code alone would plan 18 → 20 April. No trace of this input is recorded; the
recorded failure trace is Hong Kong `hk01` (see the table below and
[`TRACES.md`](TRACES.md)). A second, security-facing failure surface is the prompt-injection
suite (84 frozen cases), which must not leak the canary or system prompt.

## Evaluation scripts and results

```powershell
cd backend
$env:PYTHONPATH = "."
$env:PATHFINDER_SANDBOX = "1"
$env:DATABASE_PATH = "data/eval.db"
$env:EVAL_LIMIT = "10"   # omit for the full frozen suites
.\.venv\Scripts\python.exe scripts\run_eval.py routing injection hongkong japan travelplanner efficiency edit
```

| Item | Location |
|---|---|
| Runner | `backend/scripts/run_eval.py`, `backend/app/eval/` |
| Frozen cases | `backend/eval/fixtures/`, `backend/app/eval/datasets.py` |
| Recorded results | [docs/EVALUATION.md](../EVALUATION.md), [RESULTS.md](../eval-artifacts/v5-model-study/RESULTS.md) |
| Per-case rows | [datasets-latest.json](../eval-artifacts/v5-model-study/datasets-latest.json) |
| Claim-to-code trace index | [TRACE_INDEX.md](../eval-artifacts/v5-model-study/TRACE_INDEX.md) |

The Evaluation and Error-analysis tabs are demo/admin views; their Run button is
disabled. Bulk lists are reference-only. See the root README's
[Mocked and demo-only functions](../../README.md#mocked-and-demo-only-functions).

## Success and failure traces

| Trace | Evidence |
|---|---|
| **Success** — exported 2-day Taipei turn | [`traces/success-taipei-turn-log.json`](traces/success-taipei-turn-log.json); messages, timing, `why`, generations, tools |
| **Failure** — Hong Kong `hk01`, `claude-sonnet-5.5` | [`TRACES.md`](TRACES.md); case row with `passed:false`, `failure_class:constraint`, trace id `35164d1fe6874b7ca55a64c8307a5c47` |
| Turn-level trace model | `backend/app/core/trace.py`; SSE `trace` event; UI **Download log** |

## Requirement checklist

- [x] Source code and clear entry point (`backend/app/main.py`, root README).
- [x] Prompts/configuration (`backend/app/skills/`, `backend/app/core/prompts.py`, `.env.example`).
- [x] One ordinary task (above).
- [x] One changed request (above).
- [x] One failure case (above).
- [x] Evaluation scripts (`backend/scripts/run_eval.py`, `backend/app/eval/`).
- [x] Results (`docs/EVALUATION.md`, `docs/eval-artifacts/v5-model-study/RESULTS.md`).
- [x] Success and failure traces (above).
- [x] Submitted version identified (v5.2.11, tag `submission-v5.2.11`).
