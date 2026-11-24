# Version notes

Running notes for **Pathfinder v5**. The React UI carries the current workspace
design (map stage, day panel/rail, compact toolbar, adaptive trip-details dialog);
the core is the rebuilt backend at `backend/`.

## Flight-number edit fix — 2026-11-24

- **A flight number/name correction is now an edit, not a rebuild.** The editor
  had `swap_ticket` (a sourced replacement) but no way to apply “same times, new
  flight number”, so the model chose `scope: whole` and the planner rebuilt the
  trip. Added the `set_ticket` op (`direction: outbound|return|both`,
  `flight_number`), which keeps times, price and stops, marks the ticket
  unconfirmed and records the change.
- `diff_plans` now notices a changed ticket name/number/time, so the proposal is
  applied as a real edit.
- Tests: `test_set_ticket_changes_only_the_flight_number` (editor suite, 9/9).

## Clear details fix — 2026-11-24

- **Clear details now clears the optional trip fields.** The form already sent
  `null` for start/end date, days, party size and budget, but
  `PATCH /api/journeys/{id}/state` skipped null values, so the saved context
  restored them. A null is now an explicit clear for those five fields; fields
  absent from the patch keep their values.
- Regression test added to `test_api.py`; live API check returns all five as
  `null` with destination and currency preserved.

## Limitations and submission wording — 2026-11-24

- README now names the demo/mocked surfaces explicitly: image import, Evaluation,
  Error analysis, bulk reference lists, accounts/billing and sandbox evidence.
- Added the out-of-focus limits: non-English input was only high-level tested;
  multi-city and extremely long-day plans can time out or fall back because of the
  turn/pre-plan/answer budgets.
- Updated the interface wording to the current workspace design (map stage, day
  panel/rail, compact toolbar, adaptive trip-details dialog).
- Added `docs/submission/CODE_AND_CHECKS.md`: one ordinary task, one changed
  request, one failure case, source/prompts/config pointers, evaluation
  scripts/results, success/failure traces and the submitted version.

## Submission packaging — tag `submission-v5.2.11` — 2026-11-24

- README now maps every submission requirement to its command or file, and states
  the submitted version and tag.
- Added `backend/requirements.txt` and `requirements-dev.txt`, the tested
  dependency table, model settings, data/services/paid/hardware notes, and the
  explicit `uvicorn` entry point for port 8100.
- Added `docs/submission/`: ordinary/changed/failure inputs, trace evidence,
  external GitHub references, and the AI-assistance declaration + prompt record.
- Version metadata is now **5.2.11** in `pyproject.toml`, `/api/ready`,
  `/api/runtime` and the FastAPI docs.
- Runtime code is unchanged by this packaging pass; the tag marks this commit.

## 5.2.11 — 2026-11-24 16:05 HKT

- **Hotels fold like flights.** A stay shows the current choice with its Lock in
  button and an “N other options” disclosure; opening it lists the alternatives
  with their own lock buttons. The other stays are no longer printed in full.
- **Lock in sits at the bottom-right of a folded choice**, after the card, instead
  of under its left edge.

## 5.2.10 — 2026-11-24 15:50 HKT

- **No empty days.** The plan skill now says every date needs a scheduled stop: the
  arrival day starts at least 90 minutes after landing, the departure day ends at
  least 120 minutes before the flight. The Hong Kong 3-day first attempt used to
  leave Day 1 empty, get rejected, and pay for a second full plan.
- **Final two/three/four-day timings** (one plan generation each):
  Hong Kong 3-day **36.1 s** (29.3 s plan), Kyoto 4-day **43.4 s** (34.6 s),
  Taipei 2-day **27.4 s** (21.9 s).
- Replaying the old flight-window rule on those plans finds **4, 6 and 8** false
  boundary problems; the current rule finds **0**.
- Plan generation still dominates the turn (~22–35 s); understanding is ~7 s and
  the parallel evidence gather ~1 s.

## 5.2.9 — 2026-11-24 15:35 HKT

- **The flight window no longer rejects a valid arrival evening.** The check
  applied the **outbound departure** (from home) as a deadline in the destination
  city and the **return arrival** (back home) as an opening. An evening arrival
  therefore had an impossible window, so the planner regenerated the plan twice
  for nothing. Now only the outbound **arrival** opens the first day and the
  return **departure** closes the last day. The reported Taipei plan went from
  3 false boundary problems to **0**.
- **The two-day token cap has room for a verbose plan.** The old formula gave a
  two-day answer exactly 5,800 tokens; one attempt hit that cap, failed
  validation, and paid for a second full generation. The floor is now 8,000
  (4 days 9,000; 7 days 13,500; ceiling 16,000).
- **Measured on the same two-day Taipei request:** **133.7 s → 45.3 s**, with
  **one** plan generation (37.4 s) instead of three (37.9 + 46.6 + 41.6 s).
- Tests: `test_flight_windows.py` (9 tests), including the reported plan.

## 5.2.8 — 2026-11-24 15:25 HKT

- **Bulk tests are described accurately.** They were executed offline against
  Pathfinder before; the full corpus is too large to re-run from the page, so
  they are listed for reference only and are not the official TravelPlanner
  plan score. Copy updated in EN, 简 and 繁.

## 5.2.7 — 2026-11-24 15:20 HKT

- **Evaluation is a demo view now.** The page says so and the Run button is
  disabled, so a click cannot start an 84-case live run. A full run uses the
  configured models and providers for every case and takes a long time with
  credits; real experiments use `scripts/run_eval.py` with `EVAL_LIMIT` (and
  `PATHFINDER_SANDBOX=1` for curated evidence).
- **Bulk tests are marked “Already performed”** instead of “Reference only”.
  Their note says they were run offline, are shown for reference, and are not
  the official TravelPlanner score.
- Copy updated in EN, 简 and 繁.

## 5.2.6 — 2026-11-24 15:10 HKT

- **Evaluation scoring is named for what it is.** The score column was never
  produced by the removed judge model: `runner._row` fills it from deterministic
  checks. The panel now says **Check score** / **Check reason** (EN, 简, 繁), not
  “Judge”, and the run summary names the **verifier** model (the only second
  model in the runtime) instead of a judge.
- New runs write `judge_score` on the same 0–5 scale the panel already used
  (`score × 5`); the field name stays for old artifacts and API compatibility.
- README explains the check-based scoring.
- Wiring an actual model-graded score to the verifier was **not** added: the
  deterministic checks are cheaper and reproducible, and the verifier is already
  used for constraint audits.

## 5.2.5 — 2026-11-24 15:00 HKT

- **The judge is gone from the demo.** `LLMClient.judge`, the `JUDGE_*`
  settings and the `providers.judge` runtime entry were removed, so an old
  `.env` key cannot switch it back on. Historical `judge_score` values in
  evaluation artifacts still render as stored data; nothing calls a judge model.
- Tests: `test_the_judge_is_not_part_of_the_runtime`.

## 5.2.4 — 2026-11-24 14:55 HKT

- **A traveller who asks for a person stays in the Desk queue after their plan
  is ready.** The queue treated a finished plan as “completed”, so the held chat
  never reached the Queue lane and no Accept button appeared. A held chat is now
  live regardless of plan status, in both the Desk lane filter and the
  `/api/admin/desk` state.
- Tests: `test_a_held_chat_stays_live_after_its_plan_is_ready` in
  `test_security_fixes.py`.

## 5.2.3 — 2026-11-24 14:50 HKT

Audit of the remaining machine labels on screen.

- Prices now read **Live / Reference / Estimate** instead of the raw
  `price_kind`; travel modes read **Transit / Walk / Drive**; room and evidence
  sources, and source-link prefixes, go through the same helper, so
  `place_corpus` shows as “Place corpus”.
- The video agent has a proper label in all three languages (**Videos / 视频 /
  影片**) instead of the raw key, and the desk stop-type field reads
  “Attraction”, not `attraction`.
- `readableLabel` keeps brand spelling through a proper-name map: OSRM,
  SerpAPI, YouTube, Bilibili, Google Places, Open-Meteo, Wikipedia, LLM, HKID.
- Left raw deliberately: developer-trace fields (`phase`, `tool`, `status`) and
  diagnostics, which are operator-only and not traveller-facing labels.

## 5.2.2 — 2026-11-24 14:35 HKT

- **Machine labels read as words.** Data-source badges and the map's stop type go
  through `readableLabel`: `place_corpus` shows as “Place corpus”, `attraction`
  as “Attraction”. The helper now leaves human text alone, so “Free Wi-Fi”,
  “5-star” and “step-free” are no longer rewritten (“Free wi fi”).
- **Guide videos play again.** The 5.2.1 hardening set
  `Referrer-Policy: same-origin`, which stripped the referrer from the YouTube
  iframe and made the player fail with error 153. The policy is now
  `strict-origin-when-cross-origin`, the iframe declares the same policy, and
  “Open on” names the source properly (“YouTube”).

## 5.2.1 — 2026-11-24 14:40 HKT

A defensive pass over the demo; behaviour is unchanged for normal use.

- **Another site can no longer drive the operator API.** CORS is now an
  allow-list (`CORS_ORIGINS`, defaulting to the app and the Vite dev server)
  instead of `*`, and an operator request carrying a foreign `Origin` is
  refused even where `LOCAL_ADMIN` trusts loopback. A same-origin page still
  works, including a LAN address.
- **Forwarded-for is only trusted from the configured proxy.** A remote caller
  can no longer become the operator by sending `X-Forwarded-For: 127.0.0.1`.
- **Browser-facing hardening:** `X-Frame-Options: DENY`,
  `X-Content-Type-Options: nosniff` and `Referrer-Policy: same-origin`.
- **Imported conversations are shown, not trusted.** Their messages are marked
  `meta.imported` and are kept out of the model's history, so a shared plan file
  cannot steer later turns with an “assistant” instruction.
- **Anonymous routes are rate-limited** per address: trip creation, import and
  the planning stream, with generous demo-sized limits.
- **An oversized plan import is refused** (`MAX_IMPORT_PLAN_CHARS`), and the
  ticket booking link now passes `safeHref` like every other data link.
- **An unchanged multi-city trip is no longer rebuilt.** The shape check compares
  cities and nights with the plan's segments, so an ordinary stop edit stays an
  edit.
- **Small UI fixes:** a failed turn clears its draft preview; a day-rail drag
  released off a pill no longer swallows the next click; the saved-trip read
  sends its token in the header, not the query string; the draft map frames a
  route-only draft; “for 2 nights” is no longer read as two travellers and the
  verb “may” is no longer a month.
- Tests: `backend/tests/test_security_fixes.py` (8 tests), plus the existing
  API, partial, stream and builder suites.

## 5.2.0 — 2026-11-24 14:05 HKT

Drafts, the map, and the left column.

- **The itinerary is visible while it is written.** The composer streams a
  `plan_partial` event as each stop is confirmed: day sections with real names,
  times and coordinates, plus the trip's two ends for a flight arc. The chat,
  the Trip map, Detail view and Direct edit all show the same read-only preview;
  nothing in it can be edited, and any streaming failure still falls back to the
  whole-answer call and the deterministic plan.
- **The draft map is the real map.** With a browser map key set, the preview
  uses the same Google engine as the finished trip: numbered pins fanned apart
  when stops share an address, one dashed line and colour per day, names on
  hover. Without a key it falls back to a schematic that also groups by day and
  names only the newest stop.
- **A live reading of the message.** The composer shows small "Read as" chips
  (days, travellers, budget, date, cities) while the traveller types. It is a
  display aid; the server's analyst still decides.
- **Long plans no longer time out.** The plan token cap now scales with trip
  length (`4000 + 900 × days`, capped at 16,000), the stream's wall clock is
  tied to the turn budget, and the composer reserves time for the fast fallback
  plan. A change that alters days, dates or cities now rebuilds the trip
  (including multi-city replans) instead of asking the editor for stops that do
  not exist. A seven-day Kyoto → Taipei extension finishes in about 140 s.
- **The planner seat cannot stay stuck.** A live turn refreshes its seat on each
  keepalive; a seat untouched for 60 s is taken over, and stale seats are
  cleared at startup. A hard refresh or a deleted trip no longer answers the
  next message with "the planner is still working".
- **A smaller left column, and the day list is back.** The trip card is a 32 px
  toolbar, the vertical Day 1… rail is 176 px of navigation, and the map gained
  the space. The Trip map keeps the day's stops, times and note beside it, so
  the detail is readable without leaving the map. The floating day rail over the
  map was removed; the map's own date chips remain in its foot.
- **Trip details is a centred dialog.** It adapts from three columns to two to
  one, and its action row stays visible instead of scrolling out of reach.
- Small fixes: `buffet_restaurant` reads as "Buffet restaurant"; the message cap
  is an operator threshold; exported plan JSON carries the conversation, and an
  import rejects instruction overrides, script markup and operator claims.

Added tests: `test_partial.py`, `test_stream_equivalence.py`, `test_research.py`,
`test_speed.py`; the frontend typecheck is clean.

## 5.1.5 — 2026-11-24 10:20 HKT

- **A plan file carries the conversation.** Exporting the plan JSON now saves the
  chat beside the itinerary. Importing that file
  opens a new trip and restores those messages for reference. A file saved
  before this change, which is only the plan, still opens and simply has an
  empty chat. A broken message is skipped; it does not reject the plan.
  A hand-edited file also cannot import a system line, an instruction override
  ("ignore previous instructions", "reveal the system prompt", "developer mode"),
  or script markup. It cannot mark itself as the operator. Ordinary travel text
  is kept.

## 5.1.4 — 2026-11-24 09:50 HKT

Reviewed the new `main` commits and merged them into `pathfinder_v5`. They are
fixes on top of this v5 line, from the same repository, with tests. No secrets
and no unexpected behavior.

- **Security.** Opening a saved trip checks its owner. A guest can no longer
  claim a trip that already belongs to an account. URL intake refuses private
  hosts, unusual ports, and redirects to them. Uploads are capped, and a file
  that is not a real image is not sent to the vision model. Sign-in locks after
  repeated failures.
- **Multi-city.** One flight search for the whole trip, nights kept as given,
  and both the outbound and return legs are recorded.
- **Journeys and desk.** Proposal, choice, share, calendar, and finalised-edit
  fixes. Desk validation separates the operator note from the status, and an
  edit turn does not claim a change it did not make.
- **UI.** Model thresholds save. The mobile toolbar, desk window, and polling
  no longer drift.

## 5.1.3 — 2026-11-24 00:35 HKT

- **Length chips fill the number field.** A clarify chip such as "5 days" was stored as those words. The length box is a number field, so it stayed empty and Continue stayed disabled. The chip still reads "5 days" and now writes 5.

## 5.1.2 — 2026-11-23 23:55 HKT

A new plan now waits for the departure city, a budget, and a currency the traveller
actually named. Labels that were leaking internal keys are written for people.

### Planning gate

- **Origin, budget, and currency are required** alongside destination, start date,
  length, and party size. Hotel style and pace stay optional.
- The silent HKD default does not count. `currency_set` stays false until the
  traveller names a code, or saves one from the trip form.
- One clarify card lists every missing required field. The chat line says "a few
  details" when there is more than one.
- The welcome examples now include an exact start date, not "March", "April", or
  "next month".

### What the traveller sees

- Reservation status reads **To book**, **Optional**, or **Booked** instead of
  `to_book`.
- Google place-type chips read as words: `buffet_restaurant` becomes "Buffet
  restaurant". Names already written for a person, such as "Free Wi-Fi", are unchanged.

### Memory

- Preferences saved after a useful trip are **English sentences**. Place names or
  older chips in another language are not copied. Chips already stored in Chinese
  stay until **Clear memory**.

## 5.1.1 — 2026-11-23 15:43 HKT

Point release for the selected-item context, current-stay locking, verifier
visibility, and the new search/verifier model setup.

### Fixes

- **Selected item context in answers.** The UI selection was sent to the analyst
  but not to the answer payload, so “what is this?” ignored the selected stop.
  The ask payload now includes `selected_stop`; the answer names the selected item.
- **Locking the current hotel.** The choose endpoint only searched alternative
  hotels, so locking the stay already on the plan (Ramada Hong Kong Harbour View)
  returned “not listed”. It now accepts the current stay and keeps the other
  hotels on a multi-city trip.
- **Verifier visibility.** When a plan has no hard constraints, the “why” trace
  now says that the constraint audit was skipped. A verifier endpoint failure is
  also recorded instead of disappearing silently.

### Models and search

- Constraint verifier defaults to **DeepSeek `deepseek-flash`**, on its own
  `VERIFIER_*` endpoint, temperature 0 and a 1,500-token cap. The 20-case benchmark
  re-run scored **18/20**, the same two transfer-arithmetic false passes as before.
- Web search has a **final DeepSeek Search fallback**:
  `SerpAPI → Wikipedia → DeepSeek Search`. The model still only writes queries;
  code runs the searches. The same DeepSeek key can be used for the verifier and
  search, but the settings remain separate.
- `.env.example` is reorganized around the current design, and the local `.env`
  contains the separate verifier/search entries. Secrets remain local only.

### Mapping

- “How Pathfinder plans a trip” now uses the actual code roles (Turn orchestrator,
  Understand, Evidence workers, Composer, Validator + verifier, Plan store, Memory)
  and the UI labels (Plan, Chat, Trip map, My trips, Desk).
- The map set includes the data-flow diagram and four plain-language turn stories.

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
- Web search now has a third, final fallback: **DeepSeek Search**
  (`DEEPSEEK_SEARCH_API_KEY`, `/v1/tools/search`) runs only when SerpAPI and
  Wikipedia both return nothing. The model still only writes the query; code runs
  the search. The verifier and search key can be the same DeepSeek credential.
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

