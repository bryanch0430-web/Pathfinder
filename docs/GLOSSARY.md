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
