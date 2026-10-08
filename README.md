# Pathfinder: Agentic Trip Planner

A state-aware agentic trip planner. A **System 1 router** sends every chat message down exactly
one path (new trip / modify / quick question / clarify). **Four pre-planning agents**
(attraction, hotel, weather, ticket) run in parallel inside a time budget. A **planner** merges
their outputs and similar saved trips into a schema-validated **TripPlan**. Edits re-run only the
affected agents and preserve confirmed items.

There is no agent framework (no LangChain / LlamaIndex / CrewAI): every prompt, tool call and
model response is built, sent and logged by our own code. Model vendors and third-party APIs are
**provisional**; mock implementations make the whole system run offline with no API key.

- Design choices not fixed by the proposal: [DECISIONS.md](DECISIONS.md)
- Backend ↔ frontend contract: [contracts/openapi.json](contracts/openapi.json), [contracts/trip_plan.schema.json](contracts/trip_plan.schema.json)

---

## 1. Local setup

There is no Docker or Kubernetes, by design. Run everything locally.

### Prerequisites
- Python ≥ 3.11 and [uv](https://docs.astral.sh/uv/)
- Node ≥ 18 and npm
- PostgreSQL ≥ 15 with the [pgvector](https://github.com/pgvector/pgvector) extension (optional: set
  `PATHFINDER_STORAGE_BACKEND=memory` to run without a database)

### PostgreSQL + pgvector
```sql
-- as a superuser, after installing pgvector for your PostgreSQL version
CREATE ROLE pathfinder LOGIN PASSWORD 'pathfinder';
CREATE DATABASE pathfinder OWNER pathfinder;
\c pathfinder
CREATE EXTENSION IF NOT EXISTS vector;
```
The migration also runs `CREATE EXTENSION IF NOT EXISTS vector`, but creating an extension needs
sufficient privileges, so creating it once as a superuser avoids surprises.

### Backend
```bash
uv sync
cp .env.example .env          # edit PATHFINDER_DATABASE_URL if needed
uv run alembic -c backend/db/alembic.ini upgrade head
uv run uvicorn backend.api.main:app --reload --port 8000
```
Check it with `curl http://127.0.0.1:8000/api/health`. Interactive API docs are at `/docs`.

### Frontend
```bash
cd frontend
npm install
npm run dev                   # http://localhost:5173, proxies /api (and the WebSocket) to :8000
npm test                      # Vitest component tests (no backend needed)
```
For a quick look with no keys and no database, start the backend with
`PATHFINDER_STORAGE_BACKEND=memory uv run uvicorn backend.api.main:app --port 8000`.
After any backend schema change, regenerate the contract and the frontend types:
```bash
uv run python -m backend.api.export_contracts
cd frontend && npm run gen:types
```

### Tests
```bash
uv run pytest                 # all offline: mock models + mock providers
PATHFINDER_TEST_DATABASE_URL=postgresql+asyncpg://... uv run pytest -m postgres   # live pgvector tests
```

---

## 2. Folder guide

| Folder | What lives there |
|---|---|
| `backend/api/` | FastAPI app (`main.py`), routes and WebSocket (`routes.py`), request/response models (`schemas.py`), dependency injection (`deps.py`), contract export (`export_contracts.py`) |
| `backend/agents/` | Our own agent runtime (`runtime/`: `ModelCaller`, bounded structured repair, tool-call repair loop, parallel runner under a time budget, model clients incl. the offline mock), the System 1 router (`router.py`), the four agents (`preplanning/`), `planner.py`, the modify path (`modify.py`, `merge.py`), quick questions (`ask.py`), clarification (`clarify.py`), checks (`checks.py`), route ordering (`route_order.py`), the turn orchestrator (`orchestrator.py`), all prompts (`prompts.py`) |
| `backend/tools/` | `ToolGateway` (parse → allowlist → retry with backoff + jitter → classify → timestamp → log), `ToolRegistry` (incl. maps fallback chain), per-path allowlist, failure classification, retry policy, staleness, provider interfaces, provisional vendor stubs, mock providers + `FaultPlan` |
| `backend/db/` | SQLAlchemy models, Alembic migrations (`alembic.ini`, `migrations/`), repository protocols (`repositories/base.py`) with pgvector, in-memory and Cosmos-stub implementations; cosine similarity search with the cutoff in SQL |
| `backend/memory/` | Session store (TripPlan, history, preference profile), preference merging, saved-trip retrieval with similarity cutoff + reranker, embedding client, background writer to the long-term store |
| `backend/schemas/` | Single source of truth for every shared model: TripPlan, TripContext, routing, tool requests/outcomes, agent results, memory, observability, security, turn results |
| `backend/security/` | Injection blocklist, data fencing, canary token guard, typed-route parser, aggregate audit log (no personal data) |
| `backend/observability/` | Trace/call recorder (in-memory + JSONL + Langfuse sink) |
| `backend/eval/` | Evaluation harness: fixtures, 70/30 split, judge, the Appendix C metrics |
| `backend/tests/` | Unit tests per area + `integration/` (numbered scenarios end to end) |
| `backend/settings.py`, `backend/container.py` | All settings in one module; composition root shared by the API and the eval harness |
| `contracts/` | Exported OpenAPI + TripPlan JSON Schema (the only backend/frontend coupling) |
| `frontend/` | Vue 3 + TypeScript + Vite + Pinia dashboard; all HTTP/WebSocket access in `src/api/`, design tokens in `src/styles/tokens.css` (see `frontend/README.md`) |

---

## 3. The paths, mapped to modules

Every turn: `orchestrator.handle_turn` → `security.blocklist.screen_message` (flags only) →
`router.System1Router.route` (fenced context + TripPlan JSON + history + preferences → typed
`RouterDecision` via `security.routes.parse_router_output` → gate: threshold 0.8, missing key
variables, no plan to modify) → one path:

**New trip (`plan`)**
1. `memory.retrieval.SavedTripRetriever.retrieve`: saved trips above the similarity cutoff only.
2. `runtime.parallel.run_with_budget`: `preplanning.{Attraction,Hotel,Weather,Ticket}Agent.run` concurrently; unfinished agents are marked unavailable at the deadline.
   Each agent: the model proposes tool calls → `tools.gateway` (allowlist, validation, retry) → `runtime.tool_use.call_tool_with_repair` (≤ 2 repairs) → typed `AgentResult` with `SourceRef`s.
3. `planner.Planner.plan`: the model writes TripPlan JSON → `runtime.structured.complete_structured` (schema check, ≤ 2 repairs, else `PlanInvalidError` → error turn) → `finalise`: grounding against fetched records, `route_order.NearestNeighbourOrderer`, `plan_ops` (time slots, sections, cost), `checks.check_plan`.

**Modify (`modify`)**
1. `modify.ModifyPath.extract_change`: one model call → typed `ChangeRequest`.
2. `merge.apply_change` + `merge.agents_for_change` + `tools.staleness.sections_needing_refresh` → only the affected agents run (`run_with_budget`), plus the weather-warning cascade to the attraction agent.
3. `merge.merge_modify`: keep confirmed items, re-select hotel/tickets, enforce closures and warnings, refill and re-order touched days, cost + `check_plan`; version + 1. No planner model call.

**Quick question (`ask`)**
`ask.AskPath.run`: one model call (`AskDecision`) → `answer_from_plan` when the current plan holds a fresh fact, else exactly one gateway call on the ask allowlist → answer rendered by code with its source; relatedness to the plan is decided by code.

**Unclear / below threshold / missing variables**
`clarify.ClarifyPath.run`: one model call drafts the question (deterministic fallback); no tools, no planning.

**After a plan:** `POST /plan/confirm` sets confirmed flags; `POST /plan/feedback` (useful + 1–5) → `memory.background.BackgroundWriter` saves, embeds and writes to the long-term store off the request path.

---

## 4. Confirmed vs provisional

| Component | Status | Implementation |
|---|---|---|
| Routing, gate, clarification, 4 agents, planner, modify path, quick question | Confirmed | Real code in `backend/agents/` |
| Tool failure classification, retry/backoff/jitter, time budget, unavailable marking, staleness | Confirmed | Real code in `backend/tools/` |
| Injection defence (blocklist, fencing, typed route, allowlist, canary, aggregate audit) | Confirmed | Real code in `backend/security/`, `backend/tools/allowlist.py` |
| Session memory, background writer, similarity cutoff | Confirmed | Real code in `backend/memory/` |
| PostgreSQL + pgvector long-term store | Confirmed (team decision) | `backend/db/repositories/pg.py` + Alembic |
| Langfuse observability | Confirmed | `backend/observability/langfuse_sink.py` (enabled when keys are set) |
| Evaluation harness (Appendix C metrics) | Confirmed | `backend/eval/` |
| Router model (Jev) | **Provisional** | `JevRouterClient` stub; `MockLLMClient` |
| Agent / planner / chat LLM (Grok 4.7 via xAI) | **Provisional** | `XAIClient` stub; `MockLLMClient` |
| Judge model (e.g. GPT-4) | **Provisional** | `OpenAIJudgeClient` stub; mock judge |
| Embeddings (text-embedding-3-large) | **Provisional** | `OpenAIEmbeddingClient` stub; `HashingEmbeddingClient` |
| Reranker (Jev) | **Provisional** | `JevReranker` stub; `SimilarityReranker` |
| Maps (Google, AMap fallback), places, weather, tickets, web search APIs | **Provisional** | stubs in `backend/tools/providers/`; mock providers |
| Azure Cosmos DB | **Provisional** (alternative) | `backend/db/repositories/cosmos.py` stub |

Every provisional item has a typed interface, a config entry (`.env.example`) and a
`TODO(provisional)` marker. Selecting it raises `NotImplementedError` at startup.

---

## 5. Scenario → test map

All scenario tests run offline (`uv run pytest`). End-to-end versions are in
`backend/tests/integration/`; unit versions are in the per-area folders.

| # | Scenario | Tests |
|---|---|---|
| 1 | New trip → 4 agents in parallel → planner → schema check | `integration/test_routing.py::test_s01_*` |
| 2 | Modify (hotel, dates, budget), only responsible agents, confirmed items kept | `integration/test_routing.py::test_s02_*` |
| 3 | Quick question: from plan, or exactly one tool call; relatedness | `integration/test_routing.py::test_s03_*` |
| 4 | Unclear / below threshold → clarification, no planning | `integration/test_routing.py::test_s04_*` |
| 5 | Missing dates / party size / budget → clarification | `integration/test_routing.py::test_s05_*` |
| 6 | Time budget exceeded → unavailable, plan still returned | `integration/test_planning.py::test_s06_*` |
| 7 | Saved trips above / below similarity cutoff | `integration/test_planning.py::test_s07_*`, `memory/test_retrieval.py` |
| 8 | Schema failure → ≤ 2 repairs → error, never an invalid plan | `integration/test_planning.py::test_s08_*` |
| 9 | Nearest-neighbour daily route order | `integration/test_planning.py::test_s09_*` |
| 10 | Validation error → structured error → ≤ 2 model repairs, never resent unchanged | `integration/test_tool_failures.py::test_s10_*`, `tools/test_validation.py` |
| 11 | Timeout / rate limit / server fault → backoff + jitter in budget | `integration/test_tool_failures.py::test_s11_*`, `tools/test_retry.py` |
| 12 | Still unusable → unavailable, never filled from model knowledge | `integration/test_tool_failures.py::test_s12_*`, `tools/test_retry.py` |
| 13 | Timestamps, staleness, only the stale agent re-runs | `integration/test_tool_failures.py::test_s13_*`, `tools/test_staleness.py` |
| 14 | Blocklist screen, message still treated as data | `integration/test_security.py::test_s14_*`, `security/test_s14_blocklist_fence.py` |
| 15 | Typed route only | `integration/test_security.py::test_s15_*`, `security/test_s15_typed_route.py` |
| 16 | Per-path allowlist, blocked + logged | `integration/test_security.py::test_s16_*`, `tools/test_allowlist.py` |
| 17 | Canary token leakage detection | `integration/test_security.py::test_s17_*`, `security/test_s17_canary.py` |
| 18 | Aggregate injection logging without personal data | `integration/test_security.py::test_s18_*`, `security/test_s18_audit.py` |
| 19 | Useful plan → saved, embedded, background write | `integration/test_memory.py::test_s19_*`, `memory/test_background.py` |
| 20 | Session memory across turns | `integration/test_memory.py::test_s20_*`, `memory/test_session.py` |
| 21 | Evaluation harness | `backend/eval/` (see §6) |

---

## 6. Evaluation harness

`backend/eval/` holds the fixtures and the Appendix C metrics:

- Router labels: 80 utterances, starter set.
- Injection probes: 25, starter set.
- Hong Kong scenarios: 15, covering typhoon signal, MTR delay and venue closure.
- Japan scenarios: 18.
- TravelPlanner: loader stub only.

The metrics are routing accuracy, constraint satisfaction, error recovery, grounding, system
efficiency, edit-path efficiency and injection containment. Custom sets are split 70/30 into dev
and held-out, stratified by city and constraint type, and frozen by a saved seed file.

> **Status:** fixtures and the metric modules are in place. The injection-containment metric, the
> `python -m backend.eval.run` CLI, the frozen split file and the harness tests are still being
> completed.

---

## 7. Observability

Each turn is one trace. Every model call (exact request, response, token usage, latency) and
every tool call (raw request, status, failure kind, attempts, provider, payload, fetch time) is
recorded in memory and written as JSON lines to `var/logs/`. When
`PATHFINDER_LANGFUSE_PUBLIC_KEY` and `PATHFINDER_LANGFUSE_SECRET_KEY` are set, the same records go
to Langfuse.

---

## 8. Frontend dashboard

A three-column dashboard (`frontend/`). Everything below runs against the mock backend with no
keys and no database. Screenshots, described in text:

**Plan tab, wide screen (≥ 1280 px).** Warm off-white page; every block is a white card with
20 px corners and a faint shadow. The top bar has the round black Pathfinder compass logo on the
left, a pill tab group in the middle ("Plan" is a solid black pill, "Itinerary" and "Saved trips"
grey text), and round search and help buttons plus a peach avatar circle on the right.
- *Left column:* the chat card opens with "Hello! Where are we going?" and four outlined chips
  (Hotel, Tickets, Attractions, Weather). User messages are black bubbles on the right; assistant
  replies are light bubbles on the left, each with a grey status line under it ("Plan · 4
  agents", "Modify · hotel agent", "Quick question"). A clarification shows as a peach-edged card
  with inline date, party-size and budget inputs and a black "Continue" button, and "Needs
  clarification" under it. While a turn runs, a live bubble shows the route and the agents as
  they start and finish. The rounded input with a black circular send button sits at the bottom.
  Under the chat, "5 Days in Kyoto" (10 Nov – 14 Nov · 2 travellers · from Tokyo) lists
  Accommodation / Attractions / Tickets / Weather, each with a green tick (grey when stale or
  unavailable), followed by a black "Mark as useful" pill and a white "Trip details" pill.
- *Centre column:* a large beige map with a faint grid. The selected day's stops are numbered
  white pins joined by a dashed route from a black "H" hotel marker, and the selected stop has a
  peach ring. Round floating buttons sit in the corners: close (top left), layers (top right),
  zoom + and − (bottom right). Below the map, the place card shows "Kiyomizu-dera", "Temple ·
  ★ 4.6", and a 2 × 2 grid of icon facts: Price ¥500 / person, Opening hours, "Temperature that
  day 7–17 °C · Partly cloudy" and "≈ 15 min by transit from the hotel · estimate from
  coordinates". Each tool fact has a small "Updated …" caption, and a grey "unavailable – refresh"
  pill when the data is stale or missing.
- *Right column:* "Budget Details" has a "31% used" badge, a donut (Transport peach, Attractions
  teal, Hotel dark blue) with "Total 5-Day ¥93,300" in the centre, and a legend where Food and
  Other read "not tracked". A full-width black "Optimise my budget" pill sits below. "Travel Plan"
  has numbered day circles; the active one is black with a peach ring and the date sits under
  each circle. The "Day 1" card lists stops on a vertical line: the time on the left, then a white
  pill "Kiyomizu-dera · ¥500" with a drag handle and a lock (filled peach when confirmed).

**Medium screens (900–1279 px)** use two columns: chat and trip summary on the left; map, place
card, budget, days and timeline stacked on the right. **Phones (< 900 px)** stack everything in
one column, with the tabs on their own row under the logo. There is no horizontal scrolling at
390 px.

**Itinerary tab:** the whole trip on one card: section states with fetch times, cost table and
budget bar, constraint checks, every day with forecast and confirm checkboxes, hotel, tickets and
reservations. **Saved trips tab:** a placeholder (the API has no list endpoint) showing what this
session saved.

Keyboard: all controls are buttons or inputs with a visible blue focus ring. The tabs and day
circles use arrow keys. A stop can be reordered with Space, then the arrow keys, then Space.
Dialogs keep focus inside and close on Escape. What the API does not expose (popular times,
Food/Other costs, travel times, reordering, a saved-trips list) is listed in
[DECISIONS.md §12](DECISIONS.md#12-frontend-dashboard).
