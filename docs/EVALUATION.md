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
| Injection — containment | 84 | *see run* | 98.8 % | |
| Hong Kong — cases passed | 8 | *see run* | 8/8 | |
| Hong Kong — constraints | 32 | *see run* | 100 % | |
| Japan — cases passed | 6 | *see run* | 6/6 | |
| Japan — grounding | 100 % of items | *see run* | 100 % | |
| TravelPlanner-style — passed | 3 | **3/3** | 3/3 | 0 |
| TravelPlanner-style — constraints | 100 % | **100 %** | 100 % | 0 |

Routing, per intent (from the r3 run):

| Intent | Correct | Cases |
|---|---:|---:|
| plan | 21 | 21 |
| modify | 21 | 21 |
| ask | 21 | 21 |
| unclear | 20 | 21 |

The single miss is *"Plan something nice"* with a destination already in context: v5
reads it as `plan` (then asks for dates), while the frozen label is `unclear`.

For context, the v4 report's **live** intent-router test (Jev / fast model on the same
84 utterances) scored **92.9 %**. v5's 98.8 % is measured on the same inputs and the
same labels, with one model call per case and no rules.

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
