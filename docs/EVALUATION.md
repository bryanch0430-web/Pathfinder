# Evaluation

## 1. What is measured

The harness answers four questions:

1. Does the router choose the intended path?
2. Does a generated plan satisfy the stated constraints and keep every fact grounded?
3. Does a disruption produce a plan with a recorded gap instead of a hidden failure?
4. Does a local edit preserve everything it did not touch?

It runs the **real application pipeline** (same agents, planner, merge, validators) against
a **sandbox provider registry**. The sandbox implements the same ports as live data, so a
scored run is deterministic, offline and reproducible. It is labelled
`sandbox-offline` in every run's model snapshot. Live behaviour is exercised separately
and captured in traces and screenshots, not in these scores.

## 2. Datasets

| Dataset | Cases | What it contains |
|---|---:|---|
| `routing` | 20 | 80/20-style labelled utterances: plan, modify, ask, unclear; dev and held-out |
| `injection` | 10 | prompt overrides, hidden-instruction extraction, tool smuggling, jailbreak, canary probe |
| `hongkong` | 4 | typhoon, venue closure, MTR delay, hard budget; single and group travellers |
| `japan` | 3 | Kyoto, Tokyo, Osaka; a must-include stop and a dietary preference |
| `travelplanner` | 2 | small loader sample in the TravelPlanner style (US destinations) |

Each fixture carries `split: dev|heldout`. Defaults are frozen on first seed
(`eval_suites.frozen = 1`); a scored run against an unfrozen dataset is refused with
`409`. The CLI accepts `--split dev|heldout|all`.

The 70/30 discipline is represented in the fixture split. The shipped sets are small
starter sets; adding cases means adding a fixture and a new dataset version, never editing
a frozen one in place.

## 3. Metrics

| Metric | Definition | Unit |
|---|---|---|
| **Routing accuracy** | predicted intent == expected intent over the labelled set | % |
| **Constraint satisfaction** | case passes plan validation, budget, length and party checks | % |
| **Grounding** | sourced, non-unavailable items / all items across the run | % |
| **Error recovery** | faulty cases that still return a valid plan with the failure recorded | % |
| **Edit preservation** | a hotel swap increments the version and changes no attraction name | % |
| **Mean latency** | wall-clock per case in the sandbox | ms |

The routing set is scored with the deterministic rule router, so the number is a
reproducible lower bound, not the live Jev/writer router. The metrics are computed from
per-case score vectors stored in `EvalCaseResult.scores`; the dashboard only aggregates.

## 4. Current results (30 Oct 2026)

Latest clean runs in `data/eval/`:

| Run | Dataset | Constraint | Recovery | Grounding | Edit | Latency |
|---|---|---:|---:|---:|---:|---:|
| `baseline-v1` | hongkong | 100% | 100% | 100% | 100% | 1.5 ms |
| `hotel-agent-v2` | hongkong | 100% | 100% | 100% | 100% | 1.2 ms |
| `router-rules-v1` | routing | 80% (16/20) | — | — | — | 2 ms |
| `injection-baseline` | injection | — | — | — | 100% (10/10) | 1 ms |

Interpretation, stated plainly:

- The sandbox scores are **invariant checks**, not a claim about live data quality. A
  100% constraint/grounding score means the pipeline never produces an unsourced item,
  an over-budget plan or an invalid date shape for these cases.
- The routing lower bound is **80%**. The four misses are the ambiguous utterances in
  the dev set; the live router has Jev and the writer available, which is why the
  production path can exceed the rule baseline. The miss set is inspectable in the
  failure drawer.
- Injection containment is **100%** on the starter probes. This is a floor, not a proof
  of universal robustness.
- Latency in the sandbox is milliseconds because there is no network or model call. The
  live smoke runs took **12–20 s** for a new plan, **5 s** for a question and **12 s** for
  a hotel swap on the reference low-latency writer model. Those numbers are in the traces,
  not in this table.

## 5. Reproduce

```bash
cd backend

# one run
PYTHONPATH=. python -m app.eval_run --dataset hongkong --tag baseline-v1

# a second run; the metrics table now shows a delta against the first
PYTHONPATH=. python -m app.eval_run --dataset hongkong --tag hotel-agent-v2

# router and injection
PYTHONPATH=. python -m app.eval_run --dataset routing  --tag router-rules-v1
PYTHONPATH=. python -m app.eval_run --dataset injection --tag injection-baseline
```

Reports are written to `data/eval/<run_id>.md`. The same runs appear in the
**Evaluation** tab: metric cards with baseline/delta, experiment table, trend, dataset
split, failure drill-down and Markdown export.

API equivalent:

```bash
curl -X POST http://127.0.0.1:8100/api/eval/trials \
  -H 'Content-Type: application/json' \
  -d '{"dataset_id":"hongkong","experiment_tag":"hotel-agent-v2","split":"all"}'
```

## 6. What the sandbox actually exercises

For a planning case it:

1. geocodes through a sandbox geocoder;
2. runs the real four agents (the first place source fails once on faulty cases, which
   tests retry/fallback and gap recording);
3. composes the real planner, schedule, cost and constraints;
4. validates the plan;
5. runs a real `ModifyPath` hotel swap and checks version + preservation;
6. records per-case scores.

A failure is classified (`plan_failed`, `invalid_plan`, `over_budget`,
`wrong_length`, `wrong_party`) and carries the input, a short output, the
deterministic reason and the tool summary.

## 7. Optional LLM judge

The deterministic checks always decide pass/fail. A judge is **off by default**:
set `JUDGE_API_KEY` (plus `JUDGE_BASE_URL` and `JUDGE_MODEL`) to add an advisory
1-5 score and review flags per planning case.

- any OpenAI-compatible endpoint works; the reference is DeepSeek `deepseek-chat`
- the judge runs after the deterministic checks and never changes `passed`
- a timeout, 401 or malformed JSON returns nothing and the deterministic reason
  is kept, so an eval run never fails because of the judge
- results are cached per case payload so re-runs do not re-bill
- the model name is stored in `model_snapshot.judge`, keeping runs comparable

## 8. Limitations

- The sandbox is not a substitute for live evaluation. It cannot prove that Google
  Places, SerpApi or Open-Meteo answer correctly today.
- The starter sets are small; confidence intervals are wide.
- The rule router is scored, not the live System 1 model, for determinism.
- The LLM judge is optional and advisory. With no `JUDGE_API_KEY`, evaluation is
  fully deterministic and scores remain reproducible; with a judge enabled, a
  human spot-check is still required because the judge can be wrong too.
- Scores must not be used to tune on the held-out split. The frozen flag and the separate
  split are the guardrail.
