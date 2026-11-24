# Trace evidence

## Success trace

[`traces/success-taipei-turn-log.json`](traces/success-taipei-turn-log.json) is a
real exported turn log for:

```text
Plan a 2-day Taipei trip from Hong Kong for 1 person starting 4 December 2026, budget 12,000 TWD.
```

It records the user and assistant messages, the start/end timestamps, the
planner's `why` lines, per-generation model, latency and completion-token counts,
provider tool latencies and source data. The exported log shows **133.7 s** and
three plan attempts — the pre-5.2.9 behaviour caused by the flight-window bug.
On the submitted build the same prompt runs in **27.4 s with one plan
generation** (see the root README).

## Failure trace record

From [`datasets-latest.json`](../eval-artifacts/v5-model-study/datasets-latest.json),
Hong Kong suite, `claude-sonnet-5.5` arm, case **`hk01`**:

| Field | Value |
|---|---|
| Case | `hk01` |
| Passed | `false` |
| Failure class | `constraint` |
| Trace id | `35164d1fe6874b7ca55a64c8307a5c47` |
| Output summary | A three-day Hong Kong trip; the report says about HKD 8,170 against a HKD 9,000 budget, but a constraint check fails (`plan.constraints`), so the case is counted as a failure even though a plan was produced. |

This is a genuine expected-failure case: the planner produced a fluent plan, and
the deterministic constraint check rejected it. It is not hidden — the same row
appears in the evaluation results and the UI's case list.

## How a trace id resolves

- Each turn records a `TurnTrace` and emits a `trace` event with the request id;
  the id is also stored on every evaluation row.
- Evaluation runs live in the database named by `DATABASE_PATH` (the README uses
  `data/eval.db`). The run can be exported with
  `GET /api/eval/trials/{run_id}/export` or from the UI's **Export report**.
- The UI's per-chat **Download log** writes the same turn-log shape as the success
  trace above; it includes the plan, messages, `why` decisions, generations and
  tool timings.
- `docs/eval-artifacts/v5-model-study/TRACE_INDEX.md` maps each report claim to
  its code path and evidence file.

## Reproducing

The commands are in [the submission README](README.md#4-evaluation-commands-and-results).
Set `EVAL_LIMIT=10` for a small run; remove it for the full frozen suite.
