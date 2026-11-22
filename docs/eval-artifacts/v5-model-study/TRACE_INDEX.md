# Trace index — claim to code

The study writes per-case rows (prediction, pass/fail, latency, tokens, confidence, trace ID where available) to the JSON files in this folder and the workbook’s raw sheets. Use these paths when the report needs to connect a claim to code.

| Claim | Code | Where the evidence lives |
|---|---|---|
| One path per turn | `app/understand/pipeline.py:understand`; `app/turn/orchestrator.py` | `routing-merged.json` per-case intents |
| Jev typed route | `app/understand/jev.py:jev_route` | `routing-latest.json`, hybrid rows |
| Threshold gate | `settings.router_threshold`; route decision in pipeline | `Router_Gate` sheet, `routing-analysis.md` |
| Planner composes from evidence | `app/plan/agents.py`; `app/plan/builder.py:build_plan` | `datasets-latest.json` |
| Planner cannot invent | evidence refs / `build_plan` pool | `constraints_met`, `grounding` metrics |
| Editor is typed and bounded | `app/plan/editor.py:propose_edit`, `_apply`, locked check | `editor-direct-latest.json` |
| Verifier audits constraints | `app/plan/validator.py:audit_constraints`; `plan_text(with_sources=True)` | `verifier-latest.json` |
| Ask answers from plan | ask path in `app/turn/orchestrator.py` | `ask-adjudicated.json` |
| Memory extraction | `app/application/memory.py:derive_memory` | `memory-latest.json` |
| Effective caps | `app/core/llm.py:PURPOSE_MAX_TOKENS`, `max_tokens_for` | `cap-stress-latest.json` |
| Per-part model config | `app/api/turn.py:effective_settings`; `app/api/views.py` runtime | `models` in `/api/runtime` |
| Temperature | `app/core/llm.py:temperature_for` | Sonnet 0.0 vs 0.2 rows |
| Trace recording | `app/core/trace.py:TurnTrace`; SSE `trace` event | `trace_id` columns; `Trace` sheet |

## Sample evidence

- Routing per-case rows carry the trace's request id (`bench-<arm>-<case>`).
- Planner rows come from `app/eval/runner.py` and include the case id and eval run id; the eval DB for each arm is `backend/data/bench_<dataset>_<arm>.db`.
- Editor rows use `editor.<arm>.<case>` request ids.
- Verifier rows use `verify.<arm>.<case>`.
- Ask rows use the session id and the ask generation in the trace.
- Memory rows use `mem.<case>` request ids.

## Reproduce

```bash
cd "Pathfinder v5/backend"
PATHFINDER_SANDBOX=1 PYTHONPATH=. python scripts/v5_part_bench.py
PATHFINDER_SANDBOX=1 PYTHONPATH=. python scripts/v5_dataset_bench.py hongkong japan
PATHFINDER_SANDBOX=1 PYTHONPATH=. python scripts/v5_editor_direct.py
PATHFINDER_SANDBOX=1 PYTHONPATH=. python scripts/v5_verifier_bench.py
PATHFINDER_SANDBOX=1 PYTHONPATH=. python scripts/v5_ask_bench.py
PATHFINDER_SANDBOX=1 PYTHONPATH=. python scripts/v5_memory_bench.py
PATHFINDER_SANDBOX=1 PYTHONPATH=. python scripts/v5_cap_stress.py
PYTHONPATH=. python scripts/v5_analyze.py
PYTHONPATH=. python scripts/v5_docs.py
```
