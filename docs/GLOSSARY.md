# Pathfinder v5 — terminology

The interface and the code use the vocabulary of **COMP7608B Agentic Systems**.
This is a product glossary, not a claim that v5 implements every lecture concept.

| Course term | v5 name | Where it lives | Notes |
|---|---|---|---|
| Agent loop / turn | turn | `backend/app/turn/orchestrator.py` | One message, one intent, one bounded run. |
| Intent routing | **intent routing** | `Understand`, `RoutePayload` | Replaces the UI label “router turn”. |
| System 1 gate | **Jev** | `settings.gate`, `orchestrator.jev` | An optional decision model; it returns a choice, not a reply. |
| Intake slots | **intake slots** | `IntakeFacts`, `ClarifyPayload` | Dates, party, budget, origin, destination and constraints. |
| Runtime controller | **runtime harness** | `api/turn.py`, `Draft`, `turn_budget` | Owns deadline, seat, cancellation and SSE. |
| Tool adapter | **evidence worker** | `plan/agents.py` | Places, stays, weather, flights and videos are fixed workers, not model-chosen tools. |
| Tool policy / allowlist | **tool policy** (future) | not implemented | v5 has no per-path allowlist because the model does not choose tools yet. |
| Context | **context** | `TripContext`, `build_context`/`Context` | Profile, memory, current plan and evidence. |
| Memory | **memory** | `application/memory.py`, `profiles` | Durable preferences; near-duplicates are collapsed. |
| Evidence / grounding | **grounding** | `SourceRef`, `DataQuality` | Prices, hours, photos and links are copied from the evidence. |
| Evaluation | **evaluation** | `plan/validator.py`, `eval/` | Structure, flight windows, route continuity, constraints. |
| Flight windows | **flight windows** | `plan/flight_windows.py` | Arrival/departure buffers per travel day. |
| Route continuity | **route continuity** | `plan/continuity.py`, `plan/multicity.py` | Consecutive days and ordered city segments. |
| Repair budget | **repair limit** | `max_repairs` | A failed check returns to the composer. |
| Execution budget | **turn execution budget** | `turn_deadline_seconds`, `turn_budget` | Whole-turn deadline, default 300 s, minimum 60 s. |
| Human-in-the-loop | **handoff / desk** | `turn/orchestrator.py`, `app/learn.tsx` | A person can take over a held chat. |
| Trace | **trace** | `TurnTrace`, `RunTrace` | Why, flow, generations, tool calls and spans. |

## Code naming

Internal names that predate this glossary stay for compatibility:

- `RouterPayload` / `RoutePayload` — the intent-routing result.
- `tools` in `RunTrace` — the evidence worker/tool call log.
- `router_threshold` — the confidence threshold for intent routing.
- `turn_deadline_seconds` — the execution budget.

New UI copy should use **intent routing**, **intake slots**, **evidence
workers**, **grounded context**, **tool policy**, **execution budget** and
**route continuity**. Do not introduce new “tool workers”, “router turn” or
“route permissions” labels.

## Verification vs evaluation

| Question | Term | Code | UI label |
|---|---|---|---|
| Is this plan structurally valid? | **validator** | `plan/validator.check_structure`, `plan/builder.validate_structure` | Validation / Verification |
| Does it satisfy the traveller's hard constraints? | **constraint verifier** | `plan/validator.audit_constraints`, `skills/verify.md` | Constraint verifier |
| How does the agent perform across a dataset? | **evaluator / evaluation** | `eval/runner.py`, `eval/datasets.py` | Evaluation |

Course usage keeps these separate: a **verifier** checks an output or final state,
while an **evaluator** runs a benchmark or evaluation harness. The front page
previously used “Evaluation” for the in-turn checks; that was the wrong level and
is now **Verification**. The Settings model part is **Constraint verifier**, not
plain “Verifier”, so it cannot be confused with the offline evaluator.

## Important modules

| Module | Exact name | Entry point | Job |
|---|---|---|---|
| Intent routing | **intent router** | `understand.pipeline.understand` | Choose plan / modify / ask / clarify; extract intake slots. |
| Context | **context builder** | `_load_context`, `app/core/prompts.py` | Assemble trip state, profile, memory and evidence. |
| Evidence | **evidence workers** | `plan/agents.gather` | Fixed tool adapters: places, stays, weather, tickets, videos. |
| Planning | **planner / composer** | `plan/builder.compose` | Select and order sourced candidates. |
| Editing | **editor** | `plan/editor.propose_edit` | Turn a change request into typed operations. |
| Validation | **validator** | `plan/validator.check_structure` | Deterministic structure, hours, budget, flight windows, route continuity. |
| Constraint audit | **constraint verifier** | `plan/validator.audit_constraints` | Model judgement on the traveller's hard constraints. |
| Repair | **repair loop** | `turn/orchestrator._plan_turn` | Send failed checks back within `max_repairs`. |
| Runtime | **runtime harness** | `api/turn.py`, `turn/orchestrator.run_turn` | Deadline, seat, cancellation, SSE, trace. |
| Memory | **memory** | `application/memory.remember_trip` | Durable preferences, deduped and capped. |
| Evaluation | **evaluator** | `eval/runner.run_dataset` | Offline cases, metrics and reports. |
| Human-in-the-loop | **desk / handoff** | `api/admin.py`, `frontend/src/app/learn.tsx` | Operator takeover and supervision. |
| Tool policy | **tool policy** (future) | not implemented | Per-path allowlist; the model does not choose tools yet. |

Naming convention: UI verbs are nouns of responsibility — **Intent routing**,
**Evidence workers**, **Planner**, **Verification**, **Runtime harness**,
**Evaluation**. Code role names keep the agent suffix (`router`, `planner`,
`editor`, `validator`, `verifier`, `evaluator`), but `validator`, `verifier` and
`evaluator` are not synonyms.


## Curated place corpus

The offline evidence source is the **curated place corpus**
(`backend/app/evidence/place_corpus.py`), not an “atlas”. “Sandbox” is the runtime
mode that prevents live provider calls; the corpus is the data it uses. The corpus
contains real, source-labelled places for 12 cities plus US entries and is returned
as fallback evidence with a stale status when live data is unavailable.
