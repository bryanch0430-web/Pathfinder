# Pathfinder v5

A conversational, state-aware trip planner. **The Pathfinder interface, with a new,
LLM-first core.** The React app in `frontend/` is carried over with small settings
changes; the backend behind `/api` is rebuilt around a typed turn analyst, sourced
evidence, bounded repair and versioned plans.

**Latest:** the Models tab configures a model, temperature, reasoning effort and
reply cap for each part; the turn router can be the LLM router or Jev (System 1);
handoff goes to a real human-in-the-loop desk queue with independent switches. See
[docs/VERSION_NOTES.md](docs/VERSION_NOTES.md) — it also imports the `main` v1.x
release notes and maps each one to what v5 kept, changed, or deliberately dropped.

**Constraint verifier:** by default it uses `deepseek-flash` at
`https://api.deepseek.com/v1`, temperature 0 and a 1,500-token cap. It has its own
`VERIFIER_API_KEY` so it is not tied to the writer provider. The Settings **Models by
part → Constraint verifier** field can override the model; all other parts still share
the active writer key. With no verifier key it falls back to the default writer.

## Version notes

**v1.0.0 → v1.19.0 — 2026-11-01 to 2026-11-23 12:45 HKT (summarised).**
The legacy `main` line grew from the renamed, migrated prototype in v1.0.0 into a
complete product: identity and accounts, web search and reviews, plan / edit / clarify
turns, maps with transit, ferry and walking comparisons, budgets and currency
conversion, live flight evidence, PDF and image intake, multi-city routes, lockable
stays and legs, the studio UI and Export menu, three routing gates, a human-in-the-loop
desk with three escalation paths, traveller memory and profiles, and privacy masking.
It was checked with the offline `pytest` suite, frontend typecheck and production
build, Storybook, CI, the frozen routing / injection / Hong Kong / Japan / TravelPlanner
/ efficiency / edit-vs-replan evaluation suites, and live smoke turns. The legacy line
retained known limits (ground transport estimates, incomplete source coverage, and a
small set of stale test gaps); it is archived and is not rebuilt by v5.

> **New design direction.** After v1.19.0, the v5 redesign was proposed to handle more
> complex and ambiguous requests, and to give the runtime better context management:
> typed state, date windows, bounded execution, ordered city segments, sourced
> evidence, and explicit clarification rather than a longer prompt.

| Version | When (HKT) | Commits | Headline |
|---|---|---|---|
| **v5.1.0** | 2026-11-23 14:16 | `4995ec9`…`152fd51` | Complex requests get explicit state: date windows, flight buffers, a whole-turn budget, ordered multi-city segments, a runtime clock, split LLM/Jev confidence settings, and the complete architecture reference |
| **v5.0.0** | 2026-11-23 11:47 | `fabeb36`…`e72cfff` | LLM-first core behind the unchanged React UI: typed analyst/router, evidence workers, sourced-only planner, deterministic validation, bounded repair, versioned plans, per-part models, optional Jev and a real HITL queue |

The detailed patch notes, including every commit and the part-model study, are in
[docs/VERSION_NOTES.md](docs/VERSION_NOTES.md). `v5.0.0` is the first v5 release;
`v5.1.0` is the current line.

## What kind of agent is this?

In **COMP7608B** terms, Pathfinder v5 is an **LLM-based agentic system**: a
task-oriented, tool-using **planning agent** running under a bounded **runtime
harness**. One turn uses **routing**, **prompt chaining**, **parallelization** of
tool workers, an **evaluator–optimizer** repair loop, **skills** and **memory**, and
**human-in-the-loop** handoff. It is **not** an autonomous multi-agent swarm, and it
does **not** use a vector database/RAG for saved trips. The full mapping from course
terms (context builder, runtime/harness, observation, skill, memory types, HITL,
evaluation) to v5 code and UI is in
[docs/AGENT_TERMINOLOGY.md](docs/AGENT_TERMINOLOGY.md).


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

It tells the whole journey in plain language: you ask, Pathfinder reads the request,
gathers real evidence, builds and checks a plan, and asks a person only when it should.
Cards cover the short story, the guarantees, and when a human steps in.

The other maps are deliberately separate, so each can tell one complete story
([how to read them](docs/architecture/README.md)):

- [Under the hood: how a plan is checked](docs/architecture/pathfinder-v5-architecture-detailed.html) — sources, draft, rules, constraint audit, repair, gaps and versioning.
- [What data moves through Pathfinder](docs/architecture/pathfinder-v5-dataflow.html) — words, privacy masking, source records, plan versions, memory and replies.
- [Plan turn](docs/architecture/pathfinder-v5-flow-plan.html) · [Change turn](docs/architecture/pathfinder-v5-flow-modify.html) · [Ask turn](docs/architecture/pathfinder-v5-flow-ask.html) · [Clarify turn](docs/architecture/pathfinder-v5-flow-clarify.html)

## Why a revamp

The `main` branch (v1.18.0) works, and its `backend/app` is 56,831 Python lines:
2,479 lines of slot extraction in `application/understanding.py`, 1,031 lines of
intent rules in `application/router.py`, and 861 `re.*` uses (233 `re.compile` calls)
across 63 files deciding what a traveller meant, what a slot is, whether an edit is
safe. That core is fast and brittle — it caps intent accuracy at what the rules
anticipated and makes every new phrasing a new rule.

v5 moves the judgement into a model and keeps the *contract* in code:

| Job | main (v1.18.0) | v5 |
|---|---|---|
| What does the traveller want? | rules first, model fallback | **one typed model call** (`app/skills/understand.md`) |
| Slots, dates, constraints | regex + gazetteers | model + calendar arithmetic in code |
| Which places to include | templates + rules | model chooses **refs** from sourced candidates |
| Facts in the plan | mixed | **copied from evidence by code** |
| Edit scope | rules | model returns **typed ops**, code applies them |
| Safety | rules + model | schema + canary fence + grounded facts |
| Repair | bounded | bounded, with the validator's exact problems fed back |

There is **no regex-based understanding** anywhere in v5: `understand/` has no `re`
import, and the whole backend uses `re` in just two redaction controls
(`security/pii.py` and the admin preview endpoint) — 9 uses in 2 files. Structured
values such as `10:00-23:00` opening hours are parsed with string operations in
`plan/validator.py`, not regex.

Measured on the checked-out trees (23 Nov 2026):

| Measure | main (v1.18.0) | v5 |
|---|---:|---:|
| Python lines in `backend/app` | 56,831 | 11,990 |
| Python files | 148 | 43 |
| Files using `re.*` | 63 | 2 |
| `re.compile` calls | 233 | 4 |
| FastAPI route handlers | 100 | 98 |
| Test functions | 2,326 | 52 |
| Frontend source lines | 39,146 | 39,362 |
| Prompt packs | 24 `SKILL.md` (857 lines) | 7 (471 lines) |

The 98 v5 handlers are 95 decorated routes plus three dynamic suggestion routes; the
two missing from v5 are the SPA catch-all (replaced by a static mount) and the admin
trace-span reader. The frontend differs in 17 of its 118 main-branch source files,
plus one new Models settings file (574 lines added, 610 removed), mostly the settings
surface and its copy. The test gap is the honest cost of the rewrite: 52 test
functions against main's 2,326.

## Quick start

Requirements: Python 3.11+ (3.13 tested), Node 20+ only if you rebuild the UI.

```powershell
cd "Pathfinder v5"
copy .env.example .env      # then add a writer key (Poe, OpenAI, xAI, …)
.\start.ps1                 # http://127.0.0.1:8101
```

The same process serves the API and the built UI (`frontend/dist`). Useful flags:

```powershell
.\start.ps1 -Sandbox        # every provider answers from the curated place corpus: offline demo + evaluation
.\start.ps1 -Port 8102
```

Without a writer key the app still starts and the Settings page explains what is
missing; planning turns answer that the model is not configured.

### Development UI

```powershell
cd frontend
$env:VITE_API_TARGET = "http://127.0.0.1:8101"   # main's dev server defaults to 8100
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
4. a place the traveller names but the curated place corpus lacks is looked up by name (Google
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

## Currency

Chat currency statements are validated ISO codes, update the trip through a `context` event, and drive searchable FX widgets. See [docs/CURRENCY.md](docs/CURRENCY.md).

## Image import

A day-by-day itinerary image is parsed into `ImportedItinerary` and each named stop is sourced before the existing builder validates it. Ordinary planning is unchanged; see [docs/IMAGE_IMPORT.md](docs/IMAGE_IMPORT.md).

## Evaluation

Part-model evidence (v5, 23 Nov 2026): [TEST_PLAN](docs/eval-artifacts/v5-model-study/TEST_PLAN.md) · [RESULTS](docs/eval-artifacts/v5-model-study/RESULTS.md) · [decision record](docs/eval-artifacts/v5-model-study/PART_MODEL_DECISIONS.md) · [report evidence](docs/eval-artifacts/v5-model-study/REPORT_EVIDENCE.md) · workbook `v5_model_study.xlsx` / `.xls`.

The proposal datasets (the same frozen cases as `main`) run through the real pipeline
with sandbox evidence:

```powershell
cd backend
$env:PYTHONPATH = "."
$env:PATHFINDER_SANDBOX = "1"
$env:DATABASE_PATH = "data/eval.db"
.\.venv\Scripts\python.exe scripts\run_eval.py routing injection hongkong japan travelplanner
```

Headline results so far (22 Nov 2026, `claude-sonnet-5.5`, sandbox evidence):

| Suite | v5 | main baseline (v1.18.0) |
|---|---:|---:|
| Routing — intent accuracy (84) | **98.8 %** | 76.2 % rules (64/84) / 92.9 % live Jev (78/84) |
| TravelPlanner-style (3) | **3/3, constraints 100 %** | 3/3, 100 % |

The `main` numbers are the ones recorded on that branch: the rules layer 64/84
(76.2 %), the live Jev router 78/84 (92.9 %), injection 83/84 (98.8 %), Hong Kong
8/8, Japan 6/6 and TravelPlanner 3/3. The full table, including the cases v5 does
not yet match, is in [docs/EVALUATION.md](docs/EVALUATION.md).

The same suites are in the UI's **Evaluation** tab (operator views), with a run
report export. The architecture is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md);
what changed against `main` is in [docs/REVAMP.md](docs/REVAMP.md).

## Project layout

```
Pathfinder v5/
├── backend/
│   ├── app/
│   │   ├── api/          FastAPI routers (the existing wire contract)
│   │   ├── core/         store, model gateway, dates, traces, prompts
│   │   ├── domain/       the wire model (mirrors frontend types)
│   │   ├── evidence/     providers + curated place corpus (12 cities)
│   │   ├── plan/         agents, composer, validator, editor
│   │   ├── understand/   turn analyst schema + merge
│   │   ├── turn/         the orchestrator and its event stream
│   │   ├── eval/         datasets + runner
│   │   └── skills/       the prompt packs (edit them, or override at runtime)
│   ├── eval/fixtures/    the frozen main-branch datasets
│   ├── scripts/          smoke tests, eval runner, debug helpers
│   └── tests/
├── frontend/             the carried-over Pathfinder React UI
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
  curated place corpus and marks it (`data_status: stale`) and says so in the plan's gaps.
- The weather forecast covers 16 days; further out the plan shows "no forecast yet"
  rather than an invented one.
- The curated place corpus covers 12 cities; other destinations need Google Places.
- The editor is a model: a vague instruction can produce a broader change than
  expected, which is why every edit comes back as a tick to accept or reject.
- BYOK keys are stored in the local SQLite file and masked in every response. This
  is a single-user local app; do not expose it publicly without adding secret
  storage and HTTPS.
