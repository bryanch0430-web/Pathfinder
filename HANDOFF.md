# Handoff

**Pathfinder v5** lives at `C:\Users\Dies Irae\Desktop\HKU\LLM\Pathfinder v5`.
The existing React UI (with the v5 settings changes); a new, LLM-first backend. This
note is the shortest path for whoever picks it up next.

## Run it

```powershell
cd "Pathfinder v5"
.\start.ps1                 # http://127.0.0.1:8101 (live providers if keys are set)
.\start.ps1 -Sandbox        # curated evidence, no network
```

An older Pathfinder build may still be running on 8100; v5 uses 8101 so both can
coexist. The built UI is served by the backend; for frontend work:

```powershell
cd frontend
$env:VITE_API_TARGET = "http://127.0.0.1:8101"
npm run dev
```

## What is where

| Need | File |
|---|---|
| The whole turn | `backend/app/turn/orchestrator.py` |
| Routing / slot understanding | `backend/app/skills/understand.md`, `understand/pipeline.py` |
| Optional Jev router | `backend/app/understand/jev.py` |
| Per-part models / orchestrator choice | `backend/app/core/llm.py`, `backend/app/api/turn.py`, `frontend/src/design-system/ModelsSettings.tsx` |
| Planning prompt | `backend/app/skills/plan.md` |
| Plan construction + grounding | `backend/app/plan/builder.py` |
| Validation + constraint audit | `backend/app/plan/validator.py`, `skills/verify.md` |
| Edits | `backend/app/plan/editor.py`, `skills/edit.md` |
| Evidence providers | `backend/app/evidence/providers.py`, `atlas*.py` |
| Wire model (UI contract) | `backend/app/domain/models.py` |
| API routers | `backend/app/api/*.py` |
| Evaluation | `backend/app/eval/*`, `backend/eval/fixtures/` |
| Design notes | `docs/ARCHITECTURE.md`, `docs/REVAMP.md`, `docs/EVALUATION.md`, `docs/VERSION_NOTES.md` |
| Architecture diagram | `docs/architecture/pathfinder-v5-one-turn.png` |

## Verified so far

- 52 backend test functions in `backend/tests` (`pytest -q`).
- All 70 request paths the React app uses are served (checked against the OpenAPI
  document).
- End-to-end manual flows: plan → edit tick → accept → version 2; ask; clarify;
  injection refusal; translation (zh-Hant); segments; calendar; share.
- Evaluation (sandbox, `claude-sonnet-5.5`):
  - routing **98.8 %** (83/84) vs main rules **76.2 %** (v1.18.0)
  - travelplanner **3/3**, constraints **100 %** (parity)
  - injection and the HK/Japan plan sets: see `docs/EVALUATION.md` for the run state
    at handoff time.

## Deliberate simplifications

Read `docs/REVAMP.md` §"Deliberate reductions" before comparing to main. The big ones:
multi-city planning (one city per plan today), hotel room types, video notes,
HK weather/transit feeds, desk sentiment bands, Langfuse export, AMap.

## Safe places to change

- **Prompts**: edit `backend/app/skills/*.md`. They hot-reload on restart; an operator
  can also override them in the UI (`/api/admin/prompts`) without touching files.
- **Repair budget**: `MAX_REPAIRS` in `.env` (default 1).
- **Models**: `/api/llm/providers` + Settings → Model keys (BYOK), or the `LLM_*` /
  `OPENAI_*` / `XAI_*` blocks.
- **A new travel source**: add a provider method that returns `Evidence`, then call it
  from `plan/agents.py`. Do not let the composer see prose.

## Cautions

- Everything under `data/` is local state. Deleting the DB resets trips, keys,
  prompts overrides and eval runs.
- The app is a single-user local tool: no HTTPS, no secret vault. Do not expose it.
- Poe occasionally queues long generations for a minute or two; the client retries
  once, and a failed composer now falls back to a sourced shortlist instead of an
  empty turn.
