# Architecture

Pathfinder v5 keeps the existing **interface** and replaces the **core**. This document is
the map: what each layer owns, how one turn flows, where the guarantees live, and
what to change for the common extensions.

## 1. Layers

```
frontend/                 the carried-over React app
   │  same JSON + SSE contract
backend/app/api/          FastAPI routers, auth, admin, eval endpoints
backend/app/turn/         orchestrator: one turn → ordered events
backend/app/understand/   turn analyst: typed intent, slots, constraints, clarify
backend/app/plan/         agents, composer, validator, editor, diff
backend/app/evidence/     providers + curated atlas + Evidence model
backend/app/domain/       the wire model (mirrors frontend/src/design-system/types.ts)
backend/app/core/         store, model gateway, dates, prompts, traces
backend/app/eval/         datasets + runner
backend/app/skills/       prompt packs (Markdown, overridable at runtime)
```

Dependencies point down. The API never reaches into providers; the orchestrator never
formats SSE; the composer never touches HTTP.

## 2. One turn

`app/turn/orchestrator.py` is the whole product in one function. Events, in order:

| event | when | payload |
|---|---|---|
| `progress` | after each stage | `{phase, message, progress}` |
| `intake` | only with files/links | sources read, slots filled, places |
| `route` | after understanding | `RoutePayload` (intent, confidence, parts…) |
| `agent` | before/after each specialist | `{agent, phase, status, elapsed_ms, summary}` |
| `plan` | new plan applied | `{plan, diff}` |
| `proposal` | edit awaiting ticks | `{message_id, proposal, log}` |
| `clarify` | question instead of acting | `ClarifyPayload` |
| `token` | reply text deltas | `{text}` |
| `think` / `tool` | developer detail | model/tool timings |
| `trace` | end of turn | `RunTrace` (calls, tools, why, flow) |
| `done` / `error` | last frame | intent, request id |

Failure policy: every stage may fail; the turn still ends with `error` **or** with a
reduced result plus a visible gap. A model outage on the *understand* call becomes a
clarification question, not a lost message.

## 3. Understanding

`app/skills/understand.md` defines the contract; `app/understand/schemas.py` is the
schema; `app/understand/pipeline.py` merges the answer into `TripContext`.

- Intent is one of `plan | modify | ask | unclear`.
- Slots are partial updates; untouched fields are copied forward.
- Dates are resolved by the model against `today` and the trip timezone, then
  normalised by calendar code (`app/core/dates.py`). If only a length is known, the
  start stays null and the turn clarifies.
- `need_clarification` is the model's own judgement, plus a code check that a plan
  turn has a destination and usable dates.
- Confidence below `ROUTER_THRESHOLD` on an edit becomes a question rather than a
  risky change.

There are no keyword rules, no intent regexes, no slot gazetteers in this path.

## 4. Evidence

`app/evidence/models.py` defines `Evidence`: the only shape a fact may take on its way
to a plan. Each record carries `source_refs`, `fetched_at`, `data_status` and the
facts the provider returned.

`app/evidence/providers.py` adapts:

| Source | Used for | Fallback |
|---|---|---|
| Google Places (New) | attractions, hotels, restaurants, autocomplete, photos | atlas, `stale` |
| Open-Meteo | daily forecast | seasonal sandbox forecast |
| OSRM | walking/driving routes and geometry | straight-line estimate |
| SerpAPI Google Flights | live fares | distance-based estimate, labelled |
| SerpAPI Google | web results for answers/edits | Wikipedia |
| Wikipedia | search, page text, images | — |
| Wikipedia | named-place lookup when the atlas lacks a place | — |
| Curated atlas (12 cities) | sandbox + offline fallback | — |

Agents (`app/plan/agents.py`) run concurrently, cap what they return, mark closed
venues, and record a `DataGap` for every source that failed or returned nothing. One
source failing never stops the others.

## 5. Composer and grounding

`app/plan/builder.py`:

1. builds a compact candidate pool (`ref`, name, area, rating, price, hours, tags),
2. asks the model (`app/skills/plan.md`) for a day-by-day schedule of **refs**,
3. maps each ref back to its `Evidence` and builds `PlanItem`s from it,
4. settles the clock: sorts, attaches transfers, shifts a stop later when the walk
   between two stops would not fit,
5. computes cost lines, budget status, constraint checks, gaps and the assessment,
6. returns the plan to the orchestrator, which validates it.

A ref the pool does not contain is dropped. A stop with no source is dropped, with
one deliberate exception: when the traveller names a place and insists, the editor
may add a **user-provided** stop (`data_status: stale`, no address/hours/price, a
visible gap). That is the "no parametric filling" rule with human agency preserved.

After the cost is computed, a deterministic **budget guard** moves to a cheaper
sourced stay and then drops optional paid stops (never locked or constraint-named
ones) until the total fits, recording what it trimmed.

## 6. Validation and repair

`app/plan/validator.py` has two layers:

- **structure**: days cover the dates, no empty day, no overlap, transfer time fits,
  opening hours allow the visit, budget not exceeded, no closed stop included;
- **constraints**: the verifier skill (`app/skills/verify.md`) audits each hard
  constraint and returns pass/fail/unknown with evidence.

The orchestrator sends the problems back to the composer for at most
`MAX_REPAIRS` attempts, then ships the best plan and surfaces what remains. It never
silently drops a failed constraint.

## 7. Edits

`app/plan/editor.py` and `app/skills/edit.md`:

1. the model receives the plan outline, the candidate pool, locked items and the
   request,
2. it returns typed operations (`move`, `retime`, `remove`, `add`, `replace`,
   `swap_hotel`, `swap_ticket`, `set_field`, `note`) — or `needs_research` with the
   kind of place it needs,
3. code applies the operations, refusing to touch locked/confirmed items; a named
   place with no `ref` is added as an unconfirmed user-provided stop,
4. times, transfers and cost are recomputed, and the result becomes a **proposal**
   carrying the complete candidate plan,
5. accepting a tick saves it as the next plan version; rejecting leaves the plan
   alone.

A change to destination, dates or length is classified as a rebuild and goes through
the planning path instead.

## 8. State

SQLite (WAL) in one file:

| table | holds |
|---|---|
| `sessions` | owner token/user, held/agent, planner note |
| `contexts` | the trip form (`TripContext`) |
| `messages` | chat, with `meta` for proposals/plan decisions |
| `plans` | every version, full JSON, summary and counts |
| `shares` | read-only links with expiry |
| `profiles`, `wishlist` | traveller memory and saved trips |
| `eval_runs`, `kv`, `users`, `feedback`, `seats` | evaluation, settings/prompts/keys, accounts, one-turn-per-trip lock |

Plans are append-only versions. `restore` copies an old version forward as a new one.
Locked/confirmed flags live on the plan items.

## 9. Model gateway

`app/core/llm.py` is the only way to a model. Every call has a `purpose`, a system
skill, a user payload and — for structured calls — a Pydantic schema. JSON is parsed
defensively, validated, and repaired once with the exact validation error. Text calls
stream. Timeouts, 429/5xx retries and endpoint quirks (a provider rejecting
`response_format`) are handled in one place. Every call is recorded on the turn trace.

`app/core/prompts.py` loads skills from Markdown and applies operator overrides
(`/api/admin/prompts`). The file remains the default so a reset is always possible.

## 10. Security

- The traveller message is untrusted data in every prompt. The analyst records
  `injection_suspected`; the ask prompt states the fence explicitly.
- A canary token (`CANARY_TOKEN`) is checked on every model output; a leak becomes a
  refusal, not a reply.
- The model never chooses which tool runs; code does. There is no tool that spends
  money or books anything.
- PII redaction is a separate, operator-configurable filter with a preview endpoint.
- Session access is by owner token, signed-in user, or local admin. Operator routes
  require `X-Admin-Token` or a local/loopback caller.

## 11. Latency and cost

A planning turn is three model calls in the happy path (understand, compose, verify)
plus provider calls that run in parallel. Measured on this machine with
`claude-sonnet-5.5`: understand ≈ 3–6 s, compose ≈ 30–45 s, verify ≈ 5–8 s. An edit is
understand + edit ≈ 15–25 s. Ask turns stream after one retrieval. Sandbox providers
make agent time negligible.

The composer's output budget is deliberately large (12k tokens): the model spends part
of it on internal reasoning, and a truncated JSON costs a whole retry.

## 12. Extension points

| Want to… | Change |
|---|---|
| add a travel source | a provider method returning `Evidence`, then call it from an agent |
| change routing policy | `app/skills/understand.md` (or override at runtime) |
| change plan style | `app/skills/plan.md`, and the deterministic settlement in `builder.py` |
| add a plan field | `domain/models.py` **and** `frontend/src/design-system/types.ts` |
| add an evaluation suite | `eval/fixtures/*.json` + a `DATASETS` entry + metrics branch |
| add a model provider | `app/api/llm.py` `PROVIDERS`/`BASE_URLS` |
| add an admin view | a router in `app/api/admin.py` plus the UI screen |
