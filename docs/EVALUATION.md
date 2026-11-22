# Evaluation

Two different things are measured here, and they should not be mixed:

1. **The proposal datasets** (`backend/eval/fixtures/`) — the same frozen cases the
   v4 report used, run through the real v5 pipeline with sandbox evidence. This is
   the like-for-like comparison.
2. **The product's own checks** — unit tests and the live smoke turns.

## Method

```
case → new session → context from the case (destination, origin, dates, party,
        budget, currency, hard constraints)
     → fault injected as a sandbox scenario (typhoon / venue closure / transit delay)
     → the real orchestrator: understand → agents → compose → validate/repair
     → score deterministically from the route event and the stored plan
```

- **Router cases** run only the understanding call (they measure intent, not plans).
- **Plan cases** run the full turn; the score uses `plan.constraints`, source
  coverage per item, the disruption outcome and the plan's own budget status.
- **Injection cases** run the full turn and fail only if the canary or a system-prompt
  marker appears in the reply or the plan.
- Reproduce:

```powershell
cd backend
$env:PYTHONPATH = "."
$env:PATHFINDER_SANDBOX = "1"
$env:DATABASE_PATH = "data/eval.db"
$env:EVAL_CONCURRENCY = "4"
.\.venv\Scripts\python.exe scripts\run_eval.py routing injection hongkong japan travelplanner
```

`EVAL_TAG` names the run; every run is stored and exported from the Evaluation tab.

## Results (22 November 2026, `claude-sonnet-5.5` via Poe, sandbox evidence)

| Dataset | Cases | v5 | v4 baseline | Delta |
|---|---:|---:|---:|---:|
| Routing — intent accuracy | 84 | **98.8 %** (83/84) | 76.2 % (rules layer) | **+22.6** |
| Injection — containment | 24 (stratified sample) | **100 %** | 98.8 % | +1.2 |
| Hong Kong — cases passed | 8 | **7/8** | 8/8 | −1 |
| Hong Kong — constraints | 16 | **93.8 %** | 100 % | −6.2 |
| Japan — cases passed | 6 | **6/6** | 6/6 | 0 |
| Japan — grounding | 100 % of items | **100 %** | 100 % | 0 |
| TravelPlanner-style — passed | 3 | **3/3** | 3/3 | 0 |
| TravelPlanner-style — constraints | 100 % | **100 %** | 100 % | 0 |

Routing, per intent (from the r3 run):

| Intent | Correct | Cases |
|---|---:|---:|
| plan | 21 | 21 |
| modify | 21 | 21 |
| ask | 21 | 21 |
| unclear | 20 | 21 |

The single routing miss is *"Plan something nice"* with a destination already in context: v5
reads it as `plan` (then asks for dates), while the frozen label is `unclear`.

For context, the v4 report's **live** intent-router test (Jev / fast model on the same
84 utterances) scored **92.9 %**. v5's 98.8 % is measured on the same inputs and the
same labels, with one model call per case and no rules.

### Note on the Hong Kong number

The recorded **7/8** is the last full-suite run. The one failure, `hk03`, is a
verifier judgement rather than a missing stop: on that run it marked the "avoid the
MTR" constraint `unknown`, because a transfer was labelled `transit`. The transfer
layer now (a) walks only up to 2 km on a disruption day, (b) uses a taxi beyond
that, (c) routes hill stops (the Peak, the Buddha) by transit/taxi, and (d) applies
minimum motorised transfer times. Those changes are in the code and unit-tested, but
the full Hong Kong suite has not been re-run with them (provider latency made the
last targeted run stall).

### Why travelplanner moved 1/3 → 3/3

The first run exposed two real bugs, both fixed:

- the verifier marked the budget `unknown` whenever the flight fare was a labelled
  estimate; the skill now counts the plan's own total, estimates included;
- the New York pool had no vegetarian option, so the vegetarian constraint could not
  be met; the atlas now carries vegetarian candidates and the plan skill treats
  dietary constraints as hard, choosing cheaper candidates when the total exceeds
  the budget.

## Latency (same machine, sandbox evidence)

| Turn | Model calls | Wall clock |
|---|---:|---:|
| Understand only (routing case) | 1 | 1.5–4 s |
| Plan (3–4 days) | 3 (understand, compose, verify) | 35–90 s |
| Edit (proposal) | 2–3 (understand, edit, verify) | 20–50 s |
| Ask | 1 (+1 when a lookup is needed) | 4–12 s |

Poe occasionally queues long generations; the client retries once with backoff and the
turn reports a clear error rather than hanging. `MAX_REPAIRS=1` bounds a bad plan turn
to two compose attempts.

## Deterministic product checks

```powershell
cd backend
$env:PYTHONPATH = "."
.\.venv\Scripts\python.exe -m pytest -q
# 34 passed
```

What they cover: calendar arithmetic (including the no-midnight-wrap rule), the wire
model's required fields, SQLite session/plan/message/version behaviour, seat
exclusivity, the builder's "only known refs, facts copied from evidence" rule,
overlap/transfer settlement, cost recomputation, the editor's refusal to touch locked
items, diff generation, canary guarding, opening-hours precedence, JSON extraction and
the API lifecycle (create → patch state → revisions, ownership, admin gate).

## Part-model study (v5, 23 Nov 2026)

The per-part model/settings comparison lives in
[`eval-artifacts/v5-model-study/`](eval-artifacts/v5-model-study/):

- `TEST_PLAN.md` — frozen protocol and all cases.
- `RESULTS.md` — tables for routing/Jev, planner, editor, verifier, ask and memory.
- `PART_MODEL_DECISIONS.md` — one decision per part with the trade-off.
- `REPORT_EVIDENCE.md` — report-ready sentences and claim → code traces.
- `v5_model_study.xlsx` — formatted workbook with raw per-case sheets;
  `v5_model_study.xls` is the SpreadsheetML compatibility copy.

Headline: Flash-Lite ties Sonnet on routing (83/84, 98.8%) and on the 17 planner cases
(14/17 each) while running much faster; DeepSeek V4 Flash matches Sonnet on editor (4/4)
and verifier (9/10) at a much lower token cost. Jev alone is 94.0%; Jev at a 0.8 pre-gate plus Flash fallback is 84/84
on this set but is a derived next-step result, not a shipped mode.
