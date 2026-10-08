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
```
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
| `frontend/` | Vue 3 + TypeScript + Vite + Pinia console; all HTTP/WebSocket access in `src/api/` (see `frontend/README.md`) |

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
