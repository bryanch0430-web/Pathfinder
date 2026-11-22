# Revamp notes — v4 → v5

This is the change log that matters: what the old core did, what replaced it, and
what is honestly not at parity yet.

## Headline

| | v4 (`Pathfinder_V4/backend`) | v5 (`Pathfinder v5/backend`) |
|---|---:|---:|
| Python lines | 58,109 | **9,984** |
| Files using `re.` | **68** | **1** (`api/admin.py`, PII redaction preview) |
| `re.` occurrences | **547** | **1** |
| Understanding | rules first (547 regexes + gazetteers), model fallback | **one typed model call** |
| Facts in a plan | mixed rules + model | **copied from evidence by code** |
| Edit path | rules decide scope, model assists | **typed ops from the model, applied by code** |
| Interface | React + SSE | **the same React + the same SSE events** |

The frontend is a verbatim copy of v4's `frontend/` (only `dist/` was rebuilt). The
API contract is v4's: the same paths, the same response shapes, the same SSE event
names and payloads (see `frontend/src/lib/api.ts` and
`frontend/src/design-system/types.ts`).

## What the old core did

v4's turn was a pipeline of situation-specific modules: `understanding.py` (2,483
lines) pulled slots with regex and gazetteers, `router.py` (966 lines) scored intents
with rules, and dozens of modules (`trip_rules`, `edit_scope`, `plan_focus`,
`preferences`, `profile_rules`, `persona`, `sentiment`, `language`, …) encoded
judgements as conditions. It was fast and predictable, and it was the ceiling: a
phrasing the rules did not anticipate was misread, and every fix added a condition.

## What replaced it, by job

| Job | v5 |
|---|---|
| Read one message | `skills/understand.md` + `understand/schemas.py` + `understand/pipeline.py` |
| Merge slots, dates, constraints | `understand/pipeline.py` merge + `core/dates.py` |
| Decide clarify | the analyst's `need_clarification` + a code check for destination/dates |
| Gather evidence | `plan/agents.py` + `evidence/providers.py` (parallel, gap on failure) |
| Choose and schedule | `skills/plan.md` → refs → `plan/builder.py` builds items from evidence |
| Validate | `plan/validator.py`: structure + constraint audit (`skills/verify.md`) |
| Repair | bounded loop in `turn/orchestrator.py` with the validator's exact problems |
| Edit | `skills/edit.md` → typed ops → `plan/editor.py` → proposal ticks |
| Answer | `skills/ask.md` + plan text + retrieved sources |
| Persist | `core/store.py` (versioned plans, messages, profiles, wishlists) |
| Stream | `api/turn.py` (same SSE frames, keepalives, one turn per trip) |
| Evaluate | `eval/` + the frozen v4 fixtures |

## Design decisions

1. **One model, many roles.** Every call goes through `core/llm.py` with a `purpose`
   and a schema. Provider quirks (JSON mode rejected, rate limits, timeouts) live in
   one place. A second model (DeepSeek) is optional and used only as a judge.

2. **Grounding is structural.** The composer never writes an address, price or
   opening time: it names a `ref`, and code copies the evidence. An unknown ref is
   dropped. A stop without a source is dropped with a gap. This is why v5 can claim
   "no parametric filling" without a fact-checking regex layer.

3. **Code owns arithmetic and the clock.** Dates, day counts, overlaps, transfer
   time, budget sums and opening-hour windows are deterministic. The model is asked
   for judgement ("what belongs where and why"), not for sums.

4. **Edits are operations, not rewrites.** The model returns `move`, `retime`,
   `add`, `replace`, `swap_hotel`, …; code applies them and refuses to touch locked
   or confirmed items. A broad change is classified as a rebuild instead of being
   squeezed into an op list.

5. **Every edit is a proposal.** The UI's review mode is the default: the candidate
   plan is stored on the message and applied only when its tick is accepted.

6. **A gap is a feature.** A source that fails becomes a `DataGap` on the plan (with
   freshness and retryability), not a silent guess.

7. **Sandbox is a first-class mode.** `PATHFINDER_SANDBOX=1` swaps all travel
   providers for the curated atlas and a deterministic weather/ticket model, so the
   demo, the tests and the evaluation run the same pipeline without network flakiness.

## Deliberate reductions (not at v4 parity yet)

These are real gaps, listed so nobody is surprised:

| Area | v4 | v5 today |
|---|---|---|
| Multi-city | full segments + legs | `stops` is parsed and stored; the composer plans one city per turn |
| Hotel rooms | SerpAPI room types | `room_types` exists on the model; the endpoint returns what the plan has (often none) |
| Video notes | YouTube/Bilibili enrichment | toggles exist, no enrichment yet |
| HK-specific feeds | HKO warnings, MTR next train | disruption is modelled, but not wired to those feeds |
| Sentiment / escalation | scored per line, desk bands | desk shows calm defaults; escalation policy is stored and rendered |
| Desk automation | briefs, run, edit, undo, layout | implemented in a reduced form (see `api/admin.py`) |
| RAG corpus | embedded guide chunks + rerank | curated atlas notes + live web search |
| Langfuse | OTLP export | config accepted; no exporter yet |
| Jev System 1 | optional fast router | config accepted; off by default, the analyst is the router |
| AMap | geocode/search provider | not ported |
| Evaluation suites | 7 datasets + judge options | 7 datasets; deterministic scoring, judge optional |

Each of these is an adapter or a prompt, not a redesign: the interfaces they would
plug into already exist.

## Security changes

- The analyst is told the message is untrusted and records `injection_suspected`.
- The canary is checked on every output (`_guard_output`), and a leak becomes a
  refusal.
- The model cannot choose tools; code calls providers. There is no booking or
  payment tool.
- The API keeps v4's ownership model (guest token / signed-in user / local admin).

## Migration notes

- The `.env` keys are compatible with v4's; `Settings.writer` resolves the same
  three blocks (`OPENAI_*`, `XAI_*`, `LLM_*`). `PATHFINDER_SANDBOX` is new.
- The database schema is **not** compatible. v5 uses its own `data/pathfinder.db`.
- The UI is unchanged; run v5 on another port than a running v4 (8100) and point the
  dev server at it with `VITE_API_TARGET`.
- The frozen datasets (`backend/eval/fixtures/`) and the v4 numbers they produced are
  kept so the two implementations can be compared on the same cases.
