# Pathfinder v5

A conversational, state-aware trip planner. **Same UI as Pathfinder v4. A new,
LLM-first core.** The React app in `frontend/` is the v4 interface, unchanged; the
backend behind `/api` is rebuilt around a typed turn analyst, sourced evidence,
bounded repair and versioned plans.

**Latest:** the Models tab now configures a model, temperature and reasoning effort
for each part; the turn orchestrator can be the analyst or Jev; handoff goes to a
real desk queue with editable explicit-request phrases. See
[docs/VERSION_NOTES.md](docs/VERSION_NOTES.md) for the running version log.


```
traveller message
   │
   ├─ intake         files / links → trip facts
   ├─ understand     ONE model call → typed intent + slots + constraints + clarify
   │
   ├─ plan    agents (places · hotels · weather · tickets)  →  composer  →  validator  →  repair ≤2
   ├─ modify  agents (same evidence) → typed edit ops → validate → proposal ticks
   ├─ ask     retrieve → answer with sources
   └─ unclear clarify
   │
   └─ versioned TripPlan (SQLite) → SSE → the same React UI
```

## Architecture at a glance

An interactive diagram (dark/light, motion, export) is generated from this codebase
with [Archify](https://github.com/tt-a1i/archify):

[docs/architecture/pathfinder-v5-architecture.html](docs/architecture/pathfinder-v5-architecture.html)

It shows the turn path from the React UI through the API, turn orchestrator, analyst,
composer/editor and validator, the parallel specialist agents, the local SQLite state,
and the external model and travel providers. Cards cover grounding, state, security
and evaluation.

## Why a revamp

v4 works, and it is 57k lines of rules: hundreds of regexes deciding what a traveller
meant, what a slot is, whether an edit is safe. That core is fast and brittle — it
caps intent accuracy at what the rules anticipated and makes every new phrasing a
new rule.

v5 moves the judgement into a model and keeps the *contract* in code:

| Job | v4 | v5 |
|---|---|---|
| What does the traveller want? | rules first, model fallback | **one typed model call** (`app/skills/understand.md`) |
| Slots, dates, constraints | regex + gazetteers | model + calendar arithmetic in code |
| Which places to include | templates + rules | model chooses **refs** from sourced candidates |
| Facts in the plan | mixed | **copied from evidence by code** |
| Edit scope | rules | model returns **typed ops**, code applies them |
| Safety | rules + model | schema + canary fence + grounded facts |
| Repair | bounded | bounded, with the validator's exact problems fed back |

There is **no regex-based understanding** anywhere in v5. Regex remains only in the
PII redaction control (a security filter, not intent parsing) and in the parser for
structured values such as `10:00-23:00` opening hours.

## Quick start

Requirements: Python 3.11+ (3.13 tested), Node 20+ only if you rebuild the UI.

```powershell
cd "Pathfinder v5"
copy .env.example .env      # then add a writer key (Poe, OpenAI, xAI, …)
.\start.ps1                 # http://127.0.0.1:8101
```

The same process serves the API and the built UI (`frontend/dist`). Useful flags:

```powershell
.\start.ps1 -Sandbox        # every provider answers from the curated atlas: offline demo + evaluation
.\start.ps1 -Port 8102
```

Without a writer key the app still starts and the Settings page explains what is
missing; planning turns answer that the model is not configured.

### Development UI

```powershell
cd frontend
$env:VITE_API_TARGET = "http://127.0.0.1:8101"   # v4's dev server defaults to 8100
npm run dev
```

The Vite config already proxies `/api` and honours `VITE_API_TARGET`.

### Rebuilding the UI

```powershell
cd frontend
npm install
npm run build
```

On Windows, esbuild sometimes cannot delete its own temp file; point the build at a
local temp directory if that happens:

```powershell
$env:TEMP = "$PWD\.tmp"; $env:TMP = "$PWD\.tmp"; npm run build
```

## Configuration

Everything is read from `.env` by `backend/app/config.py`; `Settings.writer` resolves
the writer endpoint by `LLM_PROVIDER`:

| `LLM_PROVIDER` | block used |
|---|---|
| `openai` / `openai-compatible` / `chatanywhere` | `OPENAI_*` (falls back to `LLM_*`, then `POE_*`) |
| `xai` | `XAI_*` |
| anything else | `LLM_*` |

The important keys:

| Group | Keys | What it powers |
|---|---|---|
| writer | `LLM_*`, `OPENAI_*`, `XAI_*`, `POE_*` | every model call: understanding, planning, edits, answers, translation |
| places | `GOOGLE_PLACES_API_KEY`, `GOOGLE_MAPS_API_KEY` | attractions, hotels, restaurants, autocomplete, photos |
| travel | `SERPAPI_KEY`, `SERPAPI_KEY_POOL` | live flight fares, web search |
| weather | none | Open-Meteo forecast |
| judge | `JUDGE_*` | optional second model for evaluation |
| security | `CANARY_TOKEN`, `ADMIN_TOKEN`, `LOCAL_ADMIN` | injection fence, operator views |
| sandbox | `PATHFINDER_SANDBOX=1` | curated evidence, no network |

## The evidence rule

A plan may only contain facts that a source returned. The pipeline enforces it
structurally:

1. each agent turns provider answers into `Evidence` (name, address, coordinates,
   price, hours, source refs, freshness),
2. the composer sees a compact candidate pool with stable `ref` ids and may only
   choose refs,
3. code builds each `PlanItem` from the chosen evidence, so a hallucinated place,
   price or address cannot reach the plan,
4. a place the traveller names but the atlas lacks is looked up by name (Google
   Places live, Wikipedia offline) before the edit model decides;
5. if no source exists and the traveller still insists, the stop can be added as
   **user-provided and unconfirmed** — no invented address, hours or price, marked
   `stale`, with a visible gap. The traveller's decision is allowed; the uncertainty
   is not hidden;
6. anything else the sources could not confirm is shown as a `DataGap`, never filled
   from the model's memory.

A deterministic budget guard runs after the composer: it first moves to a cheaper
sourced stay, then drops optional paid stops (never a locked or constraint-named
one) until the plan fits, and records anything it trimmed.

Disruptions (a closed venue, a typhoon, a transit delay) are evidence too: the
affected venue is marked unavailable and excluded, and the recovery is recorded on
the plan.

## Evaluation

The v4 proposal datasets run through the real pipeline with sandbox evidence:

```powershell
cd backend
$env:PYTHONPATH = "."
$env:PATHFINDER_SANDBOX = "1"
$env:DATABASE_PATH = "data/eval.db"
.\.venv\Scripts\python.exe scripts\run_eval.py routing injection hongkong japan travelplanner
```

Headline results so far (22 Nov 2026, `claude-sonnet-5.5`, sandbox evidence):

| Suite | v5 | v4 baseline |
|---|---:|---:|
| Routing — intent accuracy (84) | **98.8 %** | 76.2 % (rules) / 92.9 % (live model test) |
| TravelPlanner-style (3) | **3/3, constraints 100 %** | 3/3, 100 % |

The same suites are in the UI's **Evaluation** tab (operator views), with a run
report export. Full results and the method are in
[docs/EVALUATION.md](docs/EVALUATION.md); the architecture is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); what changed against v4 is in
[docs/REVAMP.md](docs/REVAMP.md).

## Project layout

```
Pathfinder v5/
├── backend/
│   ├── app/
│   │   ├── api/          FastAPI routers (the v4 wire contract)
│   │   ├── core/         store, model gateway, dates, traces, prompts
│   │   ├── domain/       the wire model (mirrors frontend types)
│   │   ├── evidence/     providers + curated atlas (12 cities)
│   │   ├── plan/         agents, composer, validator, editor
│   │   ├── understand/   turn analyst schema + merge
│   │   ├── turn/         the orchestrator and its event stream
│   │   ├── eval/         datasets + runner
│   │   └── skills/       the prompt packs (edit them, or override at runtime)
│   ├── eval/fixtures/    the frozen v4 datasets
│   ├── scripts/          smoke tests, eval runner, debug helpers
│   └── tests/
├── frontend/             the v4 React UI, unchanged
├── docs/
└── start.ps1 / start.cmd
```

## Tests

```powershell
cd backend
$env:PYTHONPATH = "."
.\.venv\Scripts\python.exe -m pytest -q
```

Unit tests cover the date arithmetic, the wire model, the store, the plan builder's
grounding rule, the editor's preservation of locked items, the canary fence and the
SSE framing. A live smoke turn is `scripts/smoke_turn.py`.

## Known limits

- Live evidence needs provider keys; without them the app falls back to the curated
  atlas and marks it (`data_status: stale`) and says so in the plan's gaps.
- The weather forecast covers 16 days; further out the plan shows "no forecast yet"
  rather than an invented one.
- The curated atlas covers 12 cities; other destinations need Google Places.
- The editor is a model: a vague instruction can produce a broader change than
  expected, which is why every edit comes back as a tick to accept or reject.
- BYOK keys are stored in the local SQLite file and masked in every response. This
  is a single-user local app; do not expose it publicly without adding secret
  storage and HTTPS.
