# Version notes

Running notes for **Pathfinder v5**. The UI is still the existing Pathfinder React
app; the core is the rebuilt backend at `backend/`.

## 5.1.0 — 2026-11-23 14:16 HKT

Complex requests need explicit state, not a longer prompt. A request can combine a
relative period (“next month”), a length (“2 days”), a budget and currency, flights,
several cities and items already locked. The 5.0 line treated that as one generated
plan; this release gives each uncertain part its own bounded representation.

### Why the change

- **A month is not a day.** “Next month” is a date window. The 2-day length is kept,
  the window is carried into clarification, and the exact start remains the
  traveller’s choice. v5 no longer invents 1 December.
- **A turn needs a stop condition.** The runtime now enforces a whole-turn execution
  budget: 300 s default, 60 s minimum, 900 s maximum. On expiry the work is cancelled,
  the stream returns `turn_timeout`, and the seat is released.
- **A complex route is several segments.** Multi-city requests are planned one city at
  a time and joined into ordered segments. Route continuity checks dates, segment
  order and city order. Intercity transport is deliberately left as a booking gap
  until a real source is wired.
- **Flights bound the day.** Selected flight times add a 90-minute arrival and a
  120-minute departure buffer; violations return to the planner within the repair
  budget.
- **The runtime clock is authoritative.** “What is today’s date?” is answered from
  `now`, never from cached search snippets.
- **Locked choices stay visible.** Direct edit shows locked stays and flights as a
  read-only strip, and locking a return flight updates the return leg, not the
  outbound leg.

### Date windows

- “Next month” becomes **December 2026**, not 1 December.
- The clarify card asks for the **start date only** when the length is already known.
- If the date is unknown, it stays unknown; the plan is not created with a guessed
  day.

### Flight windows

- `before_flight_arrival` and `after_flight_departure` are deterministic problems.
- A day fully inside the flight window produces no false problem.
- The same checks run before repair and after the constraint audit.

### Multi-city route continuity

- `context.stops` splits the dates into ordered city segments.
- Each city is gathered and composed with the existing planner; a failed segment uses
  the existing fallback planner.
- Days, hotels, tickets, costs, constraints and gaps are merged.
- `validate_route_continuity` checks consecutive days, segment order, city order and
  day-to-segment assignment.
- Intercity transport is a `TransportLeg` plus a visible `DataGap`; it is not
  invented or priced.

### Runtime and answer quality

- The analyst may report a subjective confidence; Jev has its own acceptance gate.
  The settings hint now explains both uses.
- Current date/time answers use the injected `now` object and ignore stale search
  snippets.
- Settings labels are human-readable: **Intent confidence**, **Repair attempts**,
  **Turn execution budget**.
- Naming is separated: **validator** (deterministic), **constraint verifier** (model
  audit), **evaluator** (offline evaluation). The front-page section is
  **Verification**, not Evaluation.

### Documentation

- The interactive HTML architecture now carries a complete reference: every intent
  flow, complex-query guarantee, module, data table, API group, UI surface and known
  limit. The graph labels use the current intent-router / evidence-worker / validation
  names.
- The shared router threshold is split into two policy fields:
  **LLM router confidence** (`ANALYST_CONFIDENCE_THRESHOLD`) and **Jev confidence**
  (`JEV_CONFIDENCE_THRESHOLD`). Both are labelled non-deterministic and must be tuned
  from observed behaviour; they are not comparable and neither is a calibrated
  probability. The analyst prompt no longer receives a threshold, avoiding anchoring.
- The offline evidence source is named the **curated place corpus** in code, UI and
  diagram (`backend/app/evidence/place_corpus.py`). “Sandbox” is the runtime mode,
  not the dataset; `data_mode` is `live` or `corpus`, while the legacy `atlas`
  runtime value is still accepted for existing configurations.
- The constraint verifier defaults to **DeepSeek `deepseek-flash`** on its own
  `VERIFIER_API_KEY` endpoint, with the tested verifier settings: temperature 0 and a
  1,500-token cap. A re-run on the 20 frozen cases scored **18/20** with 2 false
  passes (v06/v14) and 0 false alarms, matching the earlier DeepSeek arm; the endpoint
  uses `deepseek-flash` because the Poe alias `deepseek-v4-flash` is not available. The writer/analyst/planner stay on the default writer; the verifier
  model can still be overridden under **Models by part**.
- The “why” trace now says when the constraint audit was skipped because the
  plan has no hard constraints, or when the verifier endpoint was unavailable. A
  plan without constraints does not call the verifier by design.
- Fixed the selected-item context in the ask path: the UI already sent the
  selected stop to the analyst, but the answer payload omitted it. “what is this?”
  now resolves to the selected item and names it in the answer.
- Fixed locking the **current** hotel. The choose endpoint only searched the
  alternative hotel list; it now accepts the stay already on the plan and preserves
  the other hotels on a multi-city trip.
- The architecture reference now includes an ordered step map, a separate
  escalation/HITL branch, a reserved Judge-model marker, and the distinction between
  reported intent confidence and calibrated probability.
- Architecture maps are rewritten as plain-language stories: a journey overview, a
  plan-checking map, a data-flow map, and four turn stories. The set is indexed in
  `docs/architecture/README.md`; the previous code-first map is replaced by the
  readable checking story.

- Retrospective dual-router shadow: 78/84 agreement, 100% accuracy on the 78 agreed
  cases, six clarifications, and the analyst's only error inside the disagreement set.
  This is a saved-prediction analysis, not a live concurrent shadow; see
  `docs/eval-artifacts/v5-model-study/shadow-router.md`.
- Live dual-router shadow (10 cases, 6 normal + 4 ambiguous): raw intent agreement
  6/10, action agreement 7/10, would clarify 7/10. All ambiguous cases converged on
  clarification; a strict agreement gate over-clarified the normal ask case. Not
  shipped; repeated shadow runs are the next step. See
  `docs/eval-artifacts/v5-model-study/shadow-live-latest.md`.
- `docs/ARCHITECTURE.md` points to that complete reference and keeps the short layer
  map.

### Limits

- Intercity flights/trains are not searched, priced or booked.
- Tool-policy allowlists remain deferred because v5 does not let the model choose
  tools.
- A multi-city plan is a first safe slice; segment-level refinement, per-city stays and
  intercity sourcing are follow-up work.

## 5.0.0 — 2026-11-23 11:47 HKT

The LLM-first revamp is the first release. One writer model (BYOK-capable) answers
every role by default; a typed analyst call decides the path; evidence is gathered
from live providers or the curated place corpus; the composer can only choose sourced
references; the plan is validated, repaired at most twice, and stored as a version.

### Models and routing

- **Models tab** (renamed from Writer): the default writer/key card, **per-part
  model**, **temperature**, **reasoning effort**, and **reply cap** for analyst,
  planner, editor, verifier, ask and memory. Blank means the default writer model
  and the call's built-in cap (planner 12,000; editor 2,000; verifier 1,500; other
  calls 4,096).
- Per-part models and tuning share the active provider key, including BYOK. Separate
  keys/providers per part are not supported yet.
- Reasoning effort is sent as `reasoning_effort` only to OpenAI reasoning models
  (GPT-5 / o-series); other providers store it but do not use that field.
- **Turn orchestrator** can be the **Analyst LLM** (default) or **Jev** (TypeSafe
  system-one). Jev chooses plan/modify/ask/clarify; the analyst still extracts slots
  and constraints, and the analyst route is kept if Jev is unreachable.
- Writer temperature now persists (`writer_call` was previously posted but ignored by
  the backend). The global reply cap is a fallback only; each part has its own
  effective cap and an optional override.
- User-facing terms follow COMP7608B: **LLM router**, **planner**, **tool workers**,
  **runtime harness**, **evaluator**, **human-in-the-loop**. See
  [AGENT_TERMINOLOGY.md](AGENT_TERMINOLOGY.md) for the full map, classification and
  the deliberate differences from the proposal (no vector RAG, no autonomous swarm).

### Grounding and evidence

- Named-place lookup: Google Places live, Wikipedia offline, acronym matching
  (`HKU`), city/disambiguation guards, and web-search fallback for described wishes
  (“a place with Gundam”).
- Place-corpus entries for Hong Kong Disneyland, HKU, Tian Tan Buddha, Ghibli Museum,
  Unicorn Gundam, and vegan venues in Tokyo/Osaka.
- Flight evidence now carries outbound and return times; plans start day 1 after
  landing and end the last day before the return flight.
- **Live providers are the default** (`PATHFINDER_SANDBOX=0`). The curated place corpus is
  an operator option under **Settings → General → Data source**, and remains the
  fallback when a live provider fails.
- Every stop and stay gets a photo. The image endpoint uses city-aware Wikipedia
  search, then Google Places, then a neutral generated tile instead of a broken
  image.

### Privacy and memory

- **PII first layer**: HKID, HK phone, email and Luhn-valid cards are masked before
  storage and before any model call. Stale patterns that cannot match a known sample
  fall back to the built-in pattern instead of silently disabling masking.
- **Memory** is extracted by the model on finalise / useful rating (durable
  preferences only) and injected into later turns; memory extraction is a background
  task so rating stays instant.

### Conversation and handoff

- Clarify chips always carry a sendable message; saved cards survive a refresh; a
  click made while the turn is closing waits for idle instead of doing nothing.
- Assistant replies render safe Markdown (bold, italic, code, links, bullet and
  numbered lists); user text stays literal.
- The default page now describes the analyst (or optional Jev), evidence agents,
  live/place-corpus grounding, photos and per-part models/memory; the old “System 1” and
  “four workers” copy is gone.
- Handoff has **three independent switches**: explicit request phrases, automatic
  handoff (frustration), and stuck chats. Each can be on or off without touching the
  other two. The phrase switch covers both the operator list and the analyst's own
  “wants a person” detection; the stuck counter counts failed edits only (ordinary
  clarifications no longer count). When a switch is off, that signal is ignored; when
  the phrase switch is off, an explicit request is answered by Pathfinder.
- A handoff goes to the **queue with no agent assigned**; a person presses **Accept**
  in the Desk to take it. Phrase requests work even when automatic handoff is off.

### Interface fixes

- Desk tiles show destination and trip dates instead of the raw UUID, with compact
  ages and ellipsis.
- Finalise is a full-size button; feedback is optimistic and the memory call no
  longer delays a star click.
- Settings tabs are now General · Handoff · Instructions · Models · Thresholds ·
  Redaction · System. Jev, reviewer and freshness controls that nothing read were
  removed from the UI and from `/api/runtime`.

### Known limits

- Per-part **separate keys/providers** are not implemented (models/tuning only).
- `reasoning_effort` is effective only on OpenAI reasoning models.
- Jev is optional and off by default; it is a router, not a replacement for the
  analyst.
- The full Hong Kong evaluation’s last recorded run was **7/8**; the transfer-realism
  changes for `hk03` are in the code but the full suite was not re-run after them.

## Imported from `main` — the v1.x release notes

`main` is the **pre-revamp line** (v1.0.0 → **v1.19.0**, 23 Nov 2026, 12:45 HKT).
Its `README.md` and `docs/CHANGELOG.md` are the product requirements the rebuild has
to preserve. They are not v5 releases; they are the behaviour v5 must not regress.

Full source:
[main README → Version notes](https://github.com/bryanch0430-web/Pathfinder/blob/main/README.md#version-notes) ·
[main CHANGELOG](https://github.com/bryanch0430-web/Pathfinder/blob/main/docs/CHANGELOG.md)

v5 keeps its own **5.x** line; the v1.x numbers are the source product line, not a
semver comparison.

### The latest legacy main note in full — v1.18.0

> More ways of saying a trip are read. A wish is not stored as the city. The clarify
> card names the end date, and a budget chip fills the box. Evaluation lists the bulk
> sets for reference only.
>
> - **A wish is not the city.** “I want to see sun cakes in Kaohsiung” stays Kaohsiung.
>   Pandas, cherry blossoms, and “built around ramen” stay a wish.
> - **More ordinary routes are read.** “Departing Charlotte for Akron”, “the 24th of
>   March to the 29th of March”, and a name that ends in City, such as Panama City.
> - **“From time to time” is still not a departure city.** Neither is “change the trip
>   from Kyoto to Osaka”.
> - **The dates form shows Start and End.** The second box is no longer an unlabelled
>   date.
> - **A budget chip fills the number box.** HKD 91,000 shows as 91000. Continue still
>   sends the currency.
> - **Evaluation has a Bulk tests column, marked reference only.** It lists the full
>   TravelPlanner set, 1,225 queries, and 1,000 English sentences each for Hong Kong,
>   Taiwan, Japan and China. Sentences that share a wording are one row. Those lists
>   are not an experiment.

| Main release | Main headline | Leveraged in v5 as | Why v5 changes the mechanism |
|---|---|---|---|
| **v1.19.0** · 2026-11-23 12:45 | Complex requests get a bounded path: date windows, flight windows, city segments and an execution budget | 5.1.0: date window, flight boundaries, multi-city segments, execution budget, clock answer | v5 gives each uncertain part explicit state instead of asking one generated plan to carry all of it; intercity transport is a gap. |
| **v1.18.0** · 2026-11-22 16:30 | A wish stays a wish, the end date is labelled, and a budget chip fills the box | A wish becomes `preferences` / `research_places` / `search_queries`, never the destination; the clarify card still names the end date; the budget chip still fills the number box | v5 has one typed analyst call, not the rule/gazetteer stack. The **product rule** (wish ≠ city) is kept; the regex that implemented it is not. |
| **v1.17.0** · 12:44 | A file can start a new trip, a date range is checked, and a found stop says why it was left out | Intake parses files; date validation rejects missing/reversed ranges; a found-but-removed stop gets a visible `DataGap` reason | LLM intake + Pydantic validation replaces special-case rules; the UI and the visible reason stay. |
| **v1.16.0** · 12:00 | A wish becomes a real stop, a finished trip stays in history, and flights fold like a meal | “Where I can see Gundam” → named lookup + web fallback; finalised trips are history; other fares fold under options; a repeated failed edit can hand off | v5 uses `research_places` and provider web search, not a street-name regex. The handoff counter now counts **failed edits**, not ordinary clarifications (the v1.16 note’s “third failure” was being over-applied). |
| **v1.15.0** · 2026-11-21 19:35 | An edit waits for a tick, a nickname finds the stop on the plan, and “how long should I stay” is the visit | Typed edit ops + proposals; full-day rule in the planner; named refs; visit length from evidence | The model proposes the edit; code applies it and protects locked/confirmed rows. The interaction is the same; the rule engine is gone. |
| **v1.14.0** · 18:10 | The map starts at the hotel, sentiment no longer hands over a chat, and Take over works on any chat | Map/hotel start; frustration is a model estimate, not sentiment rules; Desk can take a queued chat | v5 keeps Take over and the map, but automatic handoff is a master switch with a phrase list; false sentiment escalation is not repeated. |
| **v1.13.0** · 2026-11-20 15:40 | A meal change stays on the selected stop, a stop cannot sit outside the flight, and personal details are masked | PII first layer (HKID/phone/email/card); flight/arrival constraints; selected-row edit scope | Masking was **lost in the first v5 pass** and restored; the validator now enforces flight boundaries instead of rules. |
| **v1.12.0** · 2026-11-19 22:45 | A chat escalates to a live agent in three ways, and the desk shows who is waiting | Handoff master + frustration + stuck + explicit phrases; Desk Queue → Handling | v5 had made the queue “too strong” by counting clarifications and pre-assigning an agent. It now queues with no agent so a person can **Accept**, and only failed edits count. |
| v1.11.0 – v1.9.0 | Map confirmation, translation parity, error analysis, routing gates, Desk window, all eval suites | Map confirmations; plan-locale switching; eval dashboard; Desk | The UI/contract is reused; routing gates in v5 are the analyst/Jev choice, not the three main-branch gates. |
| v1.8.0 – v1.7.0 | Studio canvas UI, multi-city trips, lockable stay/leg/transfer slots, pictures/links/ranks | The React UI, SSE contract, multi-city shapes, locks, photos, source links | Copied as the interface contract; the backend behind it is new. Photos were restored with a Wikipedia → Google Places → neutral-tile resolver. |
| v1.6.x – v1.0.x | Clarify answers in their slot, flights shape the day, visible legs, honest budgets, EN/简/繁, web search, reviews | Clarify payloads; flight times and day boundaries; leg/transfer model; budget guard; locales; search providers | Product behaviour retained; implementation replaced by the typed core. |

### What was deliberately not carried over

- **The rules-first router and gazetteers** (861 `re.*` uses and 233 `re.compile`
  calls across 63 files in the old core). They were the ceiling the rebuild exists to
  remove. v5 has two regex files — `security/pii.py` and the admin redaction preview
  — nine uses in all, none of them understanding.
- **Jev as the default router.** It remains available as an explicit orchestrator
  option; the analyst is the default because it scored 98.8 % on the routing set vs
  92.9 % for the old Jev path.
- **Reviewer/model arms, freshness windows, keyword intent matching, automatic
  handoff on arbitrary keywords.** Nothing read them in v5, so their controls were
  removed. The only keyword list left is the operator’s **explicit-request phrases**,
  which is a deliberate handoff policy, not understanding.
- **Unported v1.x edge cases.** The main notes contain many narrow fixes; v5 keeps
  the product rules but not every rule implementation. The known limits above say
  where parity is still open.

### Why these changes are necessary

The main notes describe a product that works but is **brittle by construction**: a
phrasing the rules did not anticipate is misread, and every new phrasing adds a
condition. v5 keeps the same UI, the same SSE contract and the same product
promises, but moves understanding to one typed model call, facts to sourced
evidence, edits to typed operations, and handoff/settings to controls the runtime
actually reads. The changes above are the price of that core replacement; the
restorations (PII, videos, photos, settings, handoff queue, flight times) are the
places where the first pass had regressed against the main notes.

