# DECISIONS

Every choice made where the proposal ("Pathfinder: Agentic Trip Planner", 2026-10-06) and the four
team decisions are silent. Each entry says what was chosen and why. Anything marked **provisional**
is behind an interface with only a mock implementation (see the table in README.md).

## 0. Team decisions applied as given

| Decision | Where it shows |
|---|---|
| No LangChain / LlamaIndex / CrewAI or other agent framework | `backend/agents/runtime/` is our own runtime: `ModelCaller` (the one place a model is called), `complete_structured` (bounded repair), `call_tool_with_repair`, `run_with_budget`. Prompts are plain templates in `backend/agents/prompts.py`. |
| PostgreSQL + pgvector behind a repository interface (Cosmos DB as alternative) | `backend/db/repositories/base.py` (protocols), `pg.py` (SQLAlchemy async + asyncpg + pgvector), `memory.py` (in-memory), `cosmos.py` (provisional stub). Agents never import a concrete repository. |
| FastAPI backend, Vue 3 frontend, coupled only by the API contract | `contracts/openapi.json` and `contracts/trip_plan.schema.json` are exported from the backend; the frontend generates its TS types from them; all HTTP/WebSocket access is in `frontend/src/api/`. |
| No infra folder | No docker-compose, Kubernetes or deployment config. Local `uvicorn`, `npm run dev`, a locally installed PostgreSQL with pgvector; settings in `.env.example`. |

## 1. Layout

1. **One Python package rooted at the repo root.** `pyproject.toml` sits at the root and the package is `backend`, so the requested folders (`backend/api`, `backend/agents`, ...) map one to one onto import paths (`backend.agents...`). This avoids generic top-level module names (`api`, `tools`, `db`) that collide easily. Commands run from the root: `uv run uvicorn backend.api.main:app`, `uv run pytest`.
2. **`backend/settings.py`** is the single settings module (a module, not a folder), as requested ("All in one settings module").
3. **`backend/container.py`** is the composition root. Both the API (via `backend/api/deps.py`) and the evaluation harness need to build the whole system, so the wiring lives once, above both. It is the only module that names concrete implementations.
4. **`contracts/`** (repo root) holds the exported OpenAPI document and TripPlan JSON Schema: the single, explicit coupling point between backend and frontend. Regenerate with `uv run python -m backend.api.export_contracts`.
5. **Model clients live in `backend/agents/runtime/`** (`llm.py`, `caller.py`, `mock_llm.py`): the runtime owns every model call. `EmbeddingClient` and `Reranker` live in `backend/memory/` next to retrieval, their only user.
6. **Tests** live in `backend/tests/<area>/` (unit tests per module) and `backend/tests/integration/` (the numbered scenarios end to end through the orchestrator and the API). Test names carry the scenario number (`test_s07_...`).
7. **Mock providers and fault injection** live in `backend/tools/providers/mock/` (`FaultPlan`), used by tests and by the Hong Kong disruption scenarios.

## 2. Routing

1. **Router input** = trip context (form), the full current TripPlan JSON, the last `history_window` (10) turns, the preference profile and the message, each in its own fenced data block. The router model sees no other instruction channel.
2. **Gate order:** rejected/untyped output → unclear; route `unclear` → clarify; confidence < threshold (0.8) → clarify; `needs_clarification` → clarify; `plan` with missing key variables → clarify; `modify` with no plan yet → clarify ("plan one first?"). Only then is a path taken.
3. **Key trip variables** are destination, dates (start + end, or start + days), party size and budget. They come from the console form (`TripContext`), not from parsing chat text. The proposal says the console "explicitly gathers the key trip variables"; extracting them from free text would be a second, unrequested model call and a guessing surface. Missing ones are decided by code (`TripContext.missing_key_fields()`), never by the model.
4. **Clarification text** is drafted by a model call; if that call fails or its output is rejected, a deterministic template is used, and code appends any missing key variable the draft forgot to ask for.

## 3. Agents and tool use

1. **Each agent's primary tool calls are proposed by the model** (`{"tool_calls": [...]}`), because the proposal assigns tool use to the agent model and because that is where "free-text date / missing field" validation errors arise. Follow-up calls with no judgement in them are built by code: geocoding each cleaned place, place details, the destination centre for hotel distances.
2. **Tool-argument repair** uses its own call purpose (`<agent>.tool_repair`, distinct from a structured-output repair `<purpose>.repair`). An identical resubmission counts as a used repair and is not sent: "not sent again unchanged".
3. **Off-task fallback.** If the model proposes no call for the agent's own operation (for example an injected instruction made it propose only a disallowed tool), or its proposal is rejected (for example a canary leak), the agent issues its canonical, code-built request once. This implements the containment criterion "the agent still returns its own task output". It does **not** mask validation failures: an on-task call that is still invalid after two repairs leaves the section unavailable.
4. **Attraction notes cleaning is grounded by code**: a cleaned place name that does not appear in the fetched notes is dropped. If the cleaning call fails, the hit titles are used directly.
5. **Hotel ranking** score = rating − 0.25 × km from centre + 0.3 if the style matches − 1.0 if over the nightly cap. **Nightly cap** = 45 % of the total budget ÷ nights. **Rooms** = ceil(party ÷ 2).
6. **Tickets:** the earliest scheduled outbound on the start date and the latest scheduled return on the end date. Delayed or cancelled options are never chosen; each one is recorded as a `transit_delay` disruption. With no origin (or origin = destination) no tickets are searched and the section is OK with a note.
7. **Per-path allowlist:** router and clarify have no tools; plan and modify allow all five families; ask allows weather, places, maps and tickets (no web search: quick answers come from structured sources). Per agent: attraction {web_search, places, maps}, hotel {places, maps}, weather {weather}, ticket {tickets}. A call is allowed only by the intersection of path and agent.

## 4. Planner

1. **The planner model writes TripPlan JSON** (as the proposal says). Code then grounds it: every place, hotel, ticket and forecast must resolve to a record fetched by an agent in this turn, and its fields are replaced by the fetched record. Unknown entities are removed and counted (`planner_ungrounded_removed` trace event). Forecasts always come from the weather agent, never from the draft.
2. **Computed by code, not the model:** section states (from the agent results), daily route order, time slots, cost, check violations, disruption resolution, `saved_trip_refs`, version.
3. **If the model's hotel or ticket pick is missing or ungrounded**, the agent's best-ranked candidate that fits is used (the agents already ranked them).
4. **Day template:** 3 stops per day; 09:00–20:00; 2-hour visits; 30-minute transfers; first stop ≥ 60 min after outbound arrival; last stop ends ≥ 90 min before return departure. Stops that do not fit are dropped, never squeezed into an impossible slot.
5. **Route order** is greedy nearest neighbour from the hotel (`backend/agents/route_order.py`, behind the `RouteOrderer` protocol so 2-opt or a routing API can replace it).
6. **Cost** sums only prices in the plan currency (budget currency). There is no exchange-rate tool, so a price in another currency marks the cost `complete: false` instead of being converted at a guessed rate; the budget check reports a currency mismatch rather than passing silently.
7. **Saved trips** reach the planner as `SavedTripHint`s (destination, rating, similarity, place names, hotel name). They influence preference only; their places can enter a new plan only if an agent fetched them this turn (grounding).

## 5. Modify path

1. **No planner model call on the modify path:** one change-extraction call (`ChangeRequest`), only the affected agents, then deterministic check and merge. This is what makes the edit path cheaper than a full replan (Appendix C, edit-path efficiency).
2. **Affected agents** = router `affected_parts` ∪ agents implied by the structured change ∪ sections that are stale or unavailable. Dates → weather, hotel, ticket (+ attraction if the trip got longer); party size → hotel, ticket; budget → hotel; hotel style or swap → hotel; removing or adding places → attraction; explicit "re-check weather/tickets/closures" → that agent.
3. **Weather-warning cascade:** if a refreshed forecast carries a warning (e.g. typhoon signal T8) and the attraction agent was not part of the edit, it runs once more with indoor-only hints so outdoor stops that day can be replaced.
4. **Confirmed items** are explicit flags the user sets in the console (`POST /plan/confirm`). The proposal relies on "confirmed items" but does not say how items become confirmed; an explicit toggle is the smallest mechanism. Confirmed items keep their slot and time through edits unless (a) the user explicitly removes them, (b) the venue is closed that day or it is an outdoor stop under a weather warning (the item is no longer valid; a disruption is recorded), or (c) the day disappears because the trip got shorter (reported in the reply).
5. **A re-run that fails** marks its section unavailable. Data the change invalidates is dropped (forecasts and unconfirmed tickets/hotel when dates change); other earlier data is kept with its original timestamp, so the console shows its age.
6. **Item ids are stable** across date changes so confirmations stay addressable.

## 6. Quick-question path

1. **Exactly one model call** decides what data the question needs (`AskDecision`: answer, or one tool call). Code checks the current plan first, using fresh data only (not stale by the staleness limits). If the plan does not hold it, code makes exactly one tool call, with no repair loop on this path (a repair would be a second model call). A malformed model output may be repaired within `max_model_repairs`, the same as every structured output.
2. **Answers built from data are rendered by code** from the plan or the tool payload, citing the source and fetch time, so a quick answer is never model knowledge presented as a tool result.
3. **Relatedness** ("checks whether the question is related to the existing plan") is decided by code: same city as the plan's destination or origin, and dates inside the trip. An unrelated question is still answered with one tool call and carries a note saying it is not part of the current plan.

## 7. Tool failures and staleness

1. **Classification before retry:** request validation error → structured `FieldIssue`s → model repair (max 2), never resent unchanged; provider "bad request" → validation (no retry); timeout / rate limit / 5xx or malformed provider response → retried; auth / not found / provider down → permanent (no retry) → unavailable.
2. **Retry policy:** exponential backoff with full jitter (`uniform(0, min(max, base·2^(n−1)))`), honouring `retry_after`; 3 attempts, base 0.2 s, max 2 s, 4 s per attempt, 8 s per logical call, further capped by the remaining planning budget.
3. **Time budget:** one budget (20 s) for the four parallel agents. Each tool call also receives the remaining budget. The planner call is not separately bounded (model client timeouts apply).
4. **Staleness limits:** weather 6 h, tickets 1 h, hotels 24 h, attractions 7 days, against each section's oldest fetch timestamp. Stale sections are marked when a session is loaded, and refreshed on the next modify turn.
5. **Maps fallback chain:** "Google, then AMap" is an ordered provider list; the next provider is tried on any failure except a bad request. The mock chain is `mock-google` → `mock-amap`.

## 8. Security

1. **The blocklist flags and never drops.** A flagged message is still passed on as fenced trip data. Flags only feed the aggregate audit.
2. **Data fencing:** all user and tool text enters prompts inside `<untrusted_data label="...">` blocks; fence tags inside the text are defused so it cannot close the block. Every system prompt states the data rule.
3. **Typed route:** router output must be exactly one JSON object validating strictly as `RouterDecision` (no prose, no fences, no extra keys, no coercion). Anything else is rejected, audited, and treated as unclear.
4. **Canary:** a per-process random token (`PF-CANARY-<hex>`) unless configured, included in every system prompt. Every model response is string-checked (case, whitespace, zero-width and hyphen obfuscation removed); a leak rejects that output, which then falls back as described above.
5. **Aggregate audit:** counts only, keyed by (UTC day, event kind, injection category, path). The API accepts enum values only, so text and identifiers cannot be passed in.
6. **Mock "compliant" model:** `MockLLMClient(injection_compliant=True)` obeys injected instructions (free-text route, disallowed tools, canary leak), so the probes test the defences rather than the mock.

## 9. Memory and storage

1. **Similarity cutoff 0.75**, top-k 3 (the proposal names a cutoff but no value). Cosine similarity is computed in SQL with pgvector's `<=>` operator, with the cutoff in the `WHERE` clause.
2. **Embedding dimension 1536.** text-embedding-3-large supports a reduced `dimensions` parameter, and pgvector's HNSW index supports at most 2000 dimensions. Changing it needs a new migration.
3. **Offline embedder:** deterministic feature hashing (blake2b, not Python `hash()`), with each `|`-separated field normalised separately and the destination field weighted ×3, so a saved Kyoto plan and a new Kyoto request score about 0.89 and Kyoto vs Hong Kong about 0.22.
4. **Saved-trip text** uses one vocabulary for queries and saved plans: `destination | days | party | budget band | hotel style | categories | places`. AVOID constraints are left out on purpose: embedding "avoid casinos" would pull casino plans closer.
5. **Session memory is process-local** (`InMemorySessionStore`). Durable memory (useful plans, embeddings, preference profiles) goes to the long-term store through a single asyncio background worker. It embeds before writing, so a failed embedding leaves no half-saved trip. The proposal has no user accounts, so **preference profiles are keyed by session id**.
6. **Storage default** in code is `memory` (tests and no-database runs); `.env.example` selects `postgres`.
7. **Reranker:** provisionally Jev; the offline mock orders by similarity, then rating.

## 10. Observability

1. **One trace per turn**, with a 32-hex trace id that is also used as the Langfuse trace id. Every model call (`LLMCallRecord`: exact request, response, usage, latency) and tool call (`ToolCallRecord`: raw request, status, failure kind, attempts, provider, payload, `fetched_at`) is recorded, plus events (route decided, canary leak blocked, ungrounded entities removed, ...).
2. **Sinks:** an in-memory store (latest 500 traces; it feeds per-turn metrics and the grounding metric), JSON lines under `var/logs/` and Langfuse (SDK 3.x, enabled when keys are set). A failing sink never breaks a turn.
3. **The grounding metric** joins plan entities to the in-process tool log, which holds the same records sent to Langfuse, so it runs offline.
4. Embedding calls made by the background writer are not traced (they are not part of a turn).

## 11. API

1. **REST for state, WebSocket for streaming turns.** `POST /api/sessions/{id}/messages` returns the `TurnResult`; `WS /api/sessions/{id}/stream` streams `TurnEvent`s (route, agent started/finished, plan/answer/clarification/error) and ends each turn with `done` carrying the full result. `TurnEvent` is added to the OpenAPI components so the frontend can generate its type.
2. Turns within one session are serialised with a per-session `asyncio.Lock`.
3. Errors: unknown session → 404; plan action without a plan → 409; unknown item ids → 422.
