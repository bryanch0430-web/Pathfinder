# Pathfinder: Agentic Trip Planner — Final Report

**COMP7608B Final Project · Group 1**
Chan Yuk Ming · Lai Helen · Ng Tsan Fung · Wong Man Kit

Submitted version: `[TODO: tag or commit hash of the submitted snapshot]`.
All results in this report were measured on commit `25170d3` (branch `main`, 16 November 2026).
If the submitted commit differs, re-run the commands in Appendix A and update the tables.

> **Draft notes (delete before exporting to PDF).** Items marked `[TODO]` need input the
> repository cannot supply:
> 1. Section 1.3 is built from the proposal audit in `docs/TEST_CASES.md`, because the proposal
>    file is not in the repository. Check each row against the proposal itself.
> 2. Section 6.1 needs each member's contribution and the mapping from GitHub account to member.
> 3. Section 6.4 (AI use) must list every AI tool the group used. `docs/AI_USE.md` names only one.
> 4. Section 5.4 reports four regressions against the 5 November figures (hk07, i09, and both
>    token savings). Their causes, from a bisect on 17 November, are in Sections 5.4 to 5.6.
>    Fixes for 11 of the 20 failing tests exist on branch `claude/friendly-ramanujan-8ca409`
>    but are not in the measured commit; if they are merged, re-run Appendix A and update
>    Sections 5.4 to 5.6. Still to decide: how to repair the `hk07` case, how to close the
>    `i09` path, and whether to shorten the edit prompt.
> 5. No live-model evaluation and no LLM-judge run exist. Section 5 says so. If the group wants
>    either in the report, it has to be run and (for the judge) checked by a person first.

---

## 1. Users, requirements and proposal continuity

### 1.1 Users and their needs

Pathfinder is for an independent traveller who has chosen a destination but has no itinerary
(`docs/DEMO.md:26`). That traveller needs three things from one conversation:

- **A complete plan** from a short request: day-by-day stops, a stay, transport to and from the
  destination, weather and a cost check against the budget.
- **Small changes that stay small.** "Swap the hotel" should change the hotel and leave every
  other accepted item where it is.
- **Answers they can trust.** Every price, opening time and transfer time must come from a
  source the traveller can see, and a missing fact must be shown as missing.

The group did not run a user study. The needs above come from the proposal and from
`docs/SPEC.md:13-24`; this is a limitation.

### 1.2 Core requirements and scope

| Requirement | Where it is defined |
|---|---|
| Route every message to exactly one of plan, modify or ask; clarify instead of guessing | `docs/SPEC.md:13-24` |
| Gather attractions, stay, weather and transport from live sources, in parallel, inside a time budget | `docs/SPEC.md:42-52` |
| Keep a versioned plan on the server; an edit re-runs only the part it touches | `docs/SPEC.md:69` |
| Show the source and age of every fact; refresh facts that are out of date | `docs/SPEC.md:58-59` |
| Refuse instructions hidden in user text or in fetched content | `backend/app/security/guard.py` |

**Out of scope by design:** the agent never books, pays, cancels or reads a traveller's account
(`docs/SPEC.md:87`). Hotel and flight prices are reference figures with a link to the seller.
Price alerts and a mobile app are also out of scope (`docs/GAP_ANALYSIS.md:50-51`), and rail is
not connected (`README.md:463`).

### 1.3 Planned, delivered and unfinished

The proposal (9 October 2026) committed to five datasets, five metrics and an evaluation
protocol, and described eighteen design behaviours. `docs/TEST_CASES.md` gives each one an ID
(TC-01 to TC-12, DB-01 to DB-18). The table reports the state on 16 November.

| ID | Proposal commitment | Status | What was delivered, and what changed |
|---|---|---|---|
| TC-01 | TravelPlanner benchmark (about 1,225 queries) | **Not done** | A 3-case sample in the benchmark's style runs offline. The benchmark itself was not run: no adapter was built and the live model cost was not budgeted. |
| TC-02 | Japan scenarios on real, fragmented API data | **Partly** | 6 scenarios run against replayed responses. The replay file was written from the providers' documentation, not recorded from live calls. A live mode exists but is not scored. |
| TC-03 | Hong Kong disruption scenarios | Delivered, 1 case failing | 8 scenarios (typhoon, venue closure, MTR delay, source timeout). 7 of 8 pass today (Section 5.4). |
| TC-04 | Router on about 80 labelled utterances | Delivered | 84 utterances. Only the rules layer of the router is scored; the two model layers need paid keys. |
| TC-05 | 25 held-out injection probes | Delivered | 84 probes, 25 held out. |
| TC-06 | 70/30 dev/held-out split, stratified and frozen | Delivered for 4 of 7 sets | The 3 smallest sets (travelplanner, efficiency, edit_vs_replan) are not in the frozen manifest. |
| TC-07 | Constraint satisfaction, with a judge from a different model family | **Partly** | Deterministic scripts decide pass or fail. An LLM judge is implemented but was never run, and its default model is from the writer's own family (Section 5.3). |
| TC-08 | Error recovery | Delivered, 1 case failing | 6 of 7 injected faults recovered today. |
| TC-09 | Hallucination and grounding against the tool log | Delivered | Every transfer time and opening time in a plan is checked against the same run's tool log. |
| TC-10 | Efficiency: router against the full pipeline | Delivered offline | Measured with a scripted stand-in writer and estimated tokens. |
| TC-11 | Efficiency: local edit against full replan | Delivered offline, regressed | Call savings hold; token savings fell to 3.3% (Section 5.4). |
| TC-12 | Log of every search and API call | Delivered | Every tool call goes through one logged gateway. Langfuse export is optional. |
| DB-03 | Agents run inside a time budget | Partly | Only the attraction agent keeps partial results when it overruns; the others are marked unavailable. |
| DB-16 | Google Maps with AMap fallback | Partly | AMap covers geocoding and place search. The map display is Google only. |
| DB-01, 02, 04–15, 17, 18 | Remaining design behaviours | Delivered | The tests for each are listed in `docs/TEST_CASES.md:103-120`. |

The proposal set no numeric targets; it called its success criteria tentative and left the
pass mark to this report (`docs/TEST_CASES.md:21-22`). `[TODO: verify against the proposal.]`

### 1.4 Changes since the proposal

The objective did not change: one conversational agent that plans, edits and answers about a
trip. `[TODO: confirm that no change request was needed under the course procedure.]`
Three things did change.

- **Frontend rewritten.** The first build (9 October) used Vue 3. It was replaced by a React 18
  design system on 1 November (commit `cda3907`); the Vue version is kept at tag
  `vue-workspace-final`.
- **Scope grew beyond the proposal.** Multi-city trips (up to 8 cities and 30 days), lockable
  choices between hotel and flight options, user-supplied model keys, optional accounts, share
  links, and Markdown, calendar and slide exports were added (`docs/CHANGELOG.md`).
- **Evaluation narrowed to an offline sandbox.** The proposal implied live-model scoring. The
  delivered evaluation scores the agent's deterministic layers with stand-in models. This keeps
  the scored runs free and repeatable, and it is the largest gap between plan and delivery
  (Section 5.8).

---

## 2. Design and implementation

### 2.1 Workflow

Every message enters at `POST /api/turn/stream` (`backend/app/interface/api.py:1458`) and is
handled by `TurnService._run_turn` (`backend/app/application/orchestrator.py:275`). A turn
follows one fixed sequence and takes exactly one path:

```
message → screen for injection → understand (fill trip slots) → route → confidence gate
        ├─ clarify : ask one question, run no tool
        ├─ plan    : geocode → agents in parallel → compose → refine → assess → validate/repair → save v1
        ├─ modify  : read the change → re-run only the affected agent → validate → save v+1 (or hold for approval)
        └─ ask     : answer from the stored plan, or from one extra lookup
→ persist → stream the result and a trace
```

Pathfinder is **not** a free tool-calling loop. The writer model is never given function-calling
tools. Code chooses which agents and tools run, through an allowlist per path
(`backend/app/application/toolbox.py:22-74`). The model contributes through typed JSON prompts,
and code validates or caps every answer. For example, the planner prompt may only return IDs of
places the agents actually found (`backend/app/application/planner.py:591-595`). We chose this
because the traveller's main need is trustworthy facts, and a fixed pipeline makes it possible
to check every stated fact against a tool result.

### 2.2 Autonomous decisions

| Decision | Decided by | Code |
|---|---|---|
| Block a message as an injection attempt | Rules (pattern screen with severity) | `security/guard.py:94-129` |
| Intent: plan, modify, ask or unclear | Rules first; if not confident, the router model; then the writer model | `application/router.py:441-488`, `:629` |
| Ask a clarifying question instead of acting | Confidence below 0.8, or required slots missing | `application/router.py:491-532` |
| Ask before planning | Budget under USD 150 per person per night, past dates, over 30 days or 8 cities, a whole country for 4 or more days | `application/budget_check.py`, `application/country_check.py:16` |
| Which agents run | Attractions and weather always; stay if there is a night; transport if an origin is set | `application/planning.py:68-82` |
| Which agent re-runs for an edit | A fixed map from the changed part to its agent; locked items are protected | `application/modification.py:144-151` |
| What an edit means (target, operation, values) | Writer model drafts the change, with word-list rules as the fallback. The model's reading overrides the rules only at confidence 0.8 or more; an edit with no clear target below 0.4 is asked about | `application/modification.py:180-182`, `:1162-1199` |
| Day assignment and choice of stay | Writer model drafts with IDs only; code fills, balances and removes duplicates | `application/planner.py:533-697` |
| Order of stops in a day | Code: walking-time matrix and best open path, or nearest neighbour | `application/planner.py:699-722` |
| Research more, or ask the traveller | Code scores plan confidence and picks the next action | `application/assessment.py:237-262` |
| Hold an edit for approval | Code grades the size of the change; larger changes wait for the traveller to accept | `application/proposals.py:137` |

All paths in the table are under `backend/app/`.

### 2.3 Stopping and clarification rules

Every loop has a fixed bound, set in `backend/app/config.py`:

| Bound | Value | Line |
|---|---|---|
| Whole turn | 180 s, plus 90 s per extra city, at most 900 s | `:251` |
| Data gathering before planning | 90 s for all agents together | `:145` |
| Tool calls per turn | 150, plus 100 per extra city | `:204`, `:206` |
| Repairs of an invalid model answer or an invalid plan | 2 | `:144` |
| Refine rounds | 3 | `:156` |
| HTTP retries per call | 2, inside twice the 15 s timeout | `:199`, `:193` |
| Router confidence gate | 0.8 | `:140` |

Human confirmation is used at four points: clarify questions, questions before planning, held
edit proposals, and the choose, lock and "booked" controls on the plan.

### 2.4 Models and prompts

| Role | Default in code | Notes |
|---|---|---|
| Writer | DeepSeek `deepseek-chat`, temperature 0.2, 4,096 output tokens, JSON mode, 90 s timeout (`config.py:64-71`) | The group's reference configuration is xAI `grok-4.20-0309-non-reasoning`, set through `.env` (`README.md:316-318`). |
| Router model | TypeSafe Jev `jev-latest`, 8 s timeout (`config.py:81`) | Called only when the rules are not confident. |
| Judge (evaluation only) | `deepseek-chat`, temperature 0 (`config.py:102`) | Off unless a judge key is set. Never run for this report. |
| User's own key | Anthropic, OpenAI, Poe, xAI or Gemini (`infrastructure/llm_providers.py:34-52`) | Replaces the writer for that user's requests, with no fallback to the server key. |

One writer serves every prompt role in a request; there is no cheaper tier for small tasks.

Prompts are stored as eleven skill files in `backend/app/skills/<name>/SKILL.md`
(for example `route-message`, `compose-itinerary`, `edit-itinerary`, `repair-plan`). The system
prompt is the skill body plus fixed persona, language and security rules
(`backend/app/application/prompts.py:8-56`).

### 2.5 Tools, data and state

| Tool | Source | Status |
|---|---|---|
| Geocoding | Google, then AMap, Open-Meteo, Nominatim | Live |
| Places | Google Places, then AMap, Nominatim, Wikivoyage | Live |
| Weather | Open-Meteo forecast; archive beyond the forecast horizon | Live; archive values are labelled as a seasonal reference |
| Hotels, flights | SerpApi (Google Hotels and Flights) | Live reference prices, cached 6 h per session |
| Routes | Google Routes | Live; without a key, a distance-based estimate labelled `est` |
| Transfers between stops | Built-in transport model | Estimated, no network call |
| Web search | DeepSeek Search | Live; reference pages only, never a price or temperature |
| Hong Kong warnings and MTR status | Hong Kong Observatory, MTR | Live, current conditions only |
| Rail | none | Not connected |

State is kept in SQLite. Each plan version is one row keyed by session, trip and version, so
an edit never overwrites an accepted plan. Trip context, chat turns, tool-call logs, security
events and encrypted user keys have their own tables
(`backend/app/infrastructure/store.py:51-160`; user keys at `:347`).
The destination-guide retrieval corpus (`backend/app/guides/`) serves the handbook page only;
it is not used inside a planning turn.

### 2.6 Task traces

Both traces follow the sample inputs shipped in `samples/`. They are walk-throughs of the code
path. `[TODO: attach the recorded trace JSON for each from data/traces/ to the submission ZIP.]`

**Trace 1, ordinary task** (`samples/ordinary.json`: Kyoto, 4 days, 2 people, JPY 400,000, "Plan my trip.")

| Step | Code | What happens |
|---|---|---|
| 1 | `orchestrator.py:275` | Turn starts; message screened, not flagged. |
| 2 | `understanding.py:129` | The form is complete, so slots are filled by rules with no model call. |
| 3 | `router.py:629` | "plan" with full slots and no existing plan: rules decide PLAN alone, no router model call. |
| 4 | `orchestrator.py:640-649` | Budget per person per night is checked against the floor; it passes, so no question. |
| 5 | `planning.py:68`, `:363` | Four agents chosen (attractions, weather, stay, transport) and run in parallel within 90 s. |
| 6 | `planner.py:199` | Writer drafts day assignment by ID; code places the must-see stop first, orders each day, adds meals. |
| 7 | `planning.py:263-299` | Refine, assess, validate; repair at most twice if validation fails. |
| 8 | `orchestrator.py:1001` | Plan saved as version 1 and streamed with its trace. |

**Trace 2, changed request** (`samples/changed.json`: "Swap the hotel for the other option.")

| Step | Code | What happens |
|---|---|---|
| 1 | `router.py:629` | "swap" with an existing plan: rules decide MODIFY, affected part `hotel`, no model call. |
| 2 | `orchestrator.py:628-633` | Out-of-date facts on the plan are refreshed first. |
| 3 | `modification.py:1162` | Writer reads the change into a typed draft; the router's scope narrows it to the hotel. |
| 4 | `modification.py:730` | Only the stay agent runs. |
| 5 | `modification.py:2812` | Code picks a different nearby hotel; transfers are recomputed; plan validated. |
| 6 | `proposals.py:137`, `orchestrator.py:1116-1173` | In review mode the change is held until the traveller accepts; otherwise saved as version 2, every other item unchanged. |

### 2.7 Key choices and trade-offs

| Choice | Benefit | Cost |
|---|---|---|
| Fixed pipeline with the model inside typed prompts, not a tool-calling loop | Every fact can be traced to a tool result; cost and time are bounded | Less flexible; a request outside the three paths is clarified, not improvised |
| Rules-first routing | 57% of labelled messages need no router model call | Rules cover English best; 20 of 84 test messages are misrouted |
| No fallback from a user's own key | A user's data never goes to a model they did not choose | A bad key stops the model-written parts of that turn |
| Estimated transfers from a transport model | No quota use; works offline | Times are approximations and are labelled as such |
| SQLite and in-memory rate limits | No infrastructure to set up | Single machine only |

---

## 3. Reliability and recovery

Four failure situations were tested. All commands run from `backend/` with no API key and no
network. On 16 November the nine reliability test files listed in Appendix A ran together:
**182 passed, 0 failed** (65 s).

### 3.1 A data source fails or times out

- **Detection.** Every HTTP error is classified into a typed failure kind
  (`backend/app/infrastructure/http.py:109-165`).
- **Bounded recovery.** Retryable errors (timeouts, 429, 5xx) are retried at most twice with
  exponential backoff (base 0.35 s plus jitter, `http.py:29-30`). The total time is capped at
  twice the timeout (`http.py:33`), and a server's `Retry-After` is honoured only if it fits.
  Errors that cannot succeed on retry (400, 401, 403) are not repeated.
- **Safe stop.** The section becomes a visible data gap; the rest of the plan is still built.
  If the destination itself cannot be looked up, the turn ends with an error instead of a plan.
- **User feedback.** "A data source failed. Your plan was not changed."
  (`orchestrator.py:90`), with a flag saying whether asking again can help. The upstream error
  text is never shown.
- **Test.** `pytest tests/test_retry_budget.py tests/test_destination_outage_turn.py`

### 3.2 The user's own model key is rejected

- **Detection.** A provider error on a user-supplied key is raised as `CallerKeyFailed`
  (`backend/app/domain/errors.py:63`).
- **Bounded recovery.** None, deliberately. The agent does not fall back to the server's key.
  After the first failure, later model calls in the same request fail immediately without a
  network call.
- **Safe stop.** The turn finishes on the rule-based paths where it can.
- **User feedback.** One notice per request: "Your own API key failed, so this answer could not
  use your model. Check or replace the key in Settings." (`orchestrator.py:91`). The key never
  appears in the message.
- **Test.** `pytest tests/test_llm_selection.py tests/test_caller_binding.py`

### 3.3 The model returns invalid output, or the plan fails validation

- **Detection.** JSON parsing and schema validation on every model answer
  (`backend/app/application/llm_gateway.py:58-75`); plan validation after composing.
- **Bounded recovery.** At most 2 repair prompts, each quoting the error. A repair that
  removes an item, or adds a place or link the original plan did not have, is refused; prices
  it changed are put back (`backend/app/application/validation.py:273-282`, `:327`).
- **Safe stop.** After the second failed repair the turn ends with a `plan_invalid` error and
  nothing is saved.
- **User feedback.** A retryable error message in the conversation.
- **Test.** `pytest tests/test_proposal_design.py`

### 3.4 A turn runs too long

- **Detection.** A deadline on the response stream (180 s for one city).
- **Bounded recovery.** None; the turn task and its agents are cancelled.
- **Safe stop.** Keep-alive signals are sent while waiting, then exactly one `turn_timeout`
  event closes the stream.
- **User feedback.** "This took too long and was stopped. Please try again."
  (`backend/app/interface/api.py:1528`).
- **Test.** `pytest tests/test_sse_stream.py`

Two related stops are tested in the same run: a request that is infeasible before planning
(budget too low, more than 30 days or 8 cities) produces a question and **zero tool calls**
(`tests/test_budget_check.py`, `tests/test_trip_too_large.py`); and a tool call beyond the
per-turn cap is refused before it reaches the source (`tests/test_retry_budget.py`, tested
with the cap set to 3).

### 3.5 Risks from repeated external actions

Pathfinder is plan-only, so a repeated action can never book or pay twice. The remaining risk
is repeated paid lookups. It is bounded by the per-turn tool cap, the 6-hour search cache for
hotels and flights, a 10-minute minimum between re-fetches of a failed section
(`backend/app/application/freshness.py:57`), and a rate limit of 30 AI requests per minute per
client. Paid or rate-sensitive sources (Grok web search, Nominatim, Wikimedia) are never
retried.

Known gaps:

- A model call that times out may still be billed, and a retry sends it again. One JSON answer
  can cost up to 9 requests in the worst case (3 attempts, each repaired twice).
- There is no idempotency key. A traveller who resends a message runs the pipeline again.
- There is no running cost meter or per-user spending cap, only call caps.
- When the writer model fails mid-plan, the plan is completed by rules without telling the
  traveller that the model was not used. We found no test for this path.

---

## 4. Non-functional requirements

Operating assumptions: one server process on one machine, run locally for the demo; no
concurrency target was set. API keys are optional; without them the agent uses free sources
and rule-based planning.

| Quality | Target or check | Measure implemented | Result | Trade-off |
|---|---|---|---|---|
| Latency | A typical new plan in 12–20 s (`README.md:848`); hard stop at 180 s | Parallel agents under a 90 s budget; streaming with keep-alives; turn deadline | Sandbox: 72–119 ms per case with stand-in sources (run reports, Appendix A). Live: 12–20 s for a plan, about 5 s for a question, observed informally during development (`docs/EVALUATION.md:155`). **No p50/p95 benchmark was run.** | An agent that overruns loses its section |
| Cost | Fewer calls than running the full pipeline on every message | Rules-first routing; local edits; tool-call cap; 6 h search cache | Router path: 69.6% fewer calls. Local edit: 84.5% fewer calls (Section 5.4). No live cost in money was measured. | Token savings are small (10.2% and 3.3%) |
| Workload | Single node | SQLite in WAL mode; per-IP rate limits (30 AI requests/min); 1 MiB body limit | No load test was run | Limits reset on restart; cannot scale out |
| Privacy | User keys never readable or logged | Keys encrypted at rest (Fernet), only the last characters shown; secrets scrubbed from error text; passwords hashed with scrypt; share links hide budget and party size | Covered by `tests/test_credential_store.py`, `tests/test_llm_keys_api.py` | Message text is sent to the chosen model and search providers |
| Security | Contain at least 95% of injection probes | Pattern screen, fenced user text, tool allowlist per path, canary token, screening of fetched text | 97.6% contained (82 of 84) with a worst-case stand-in writer | Pattern screen is English only |
| Safe startup | Never run on default secrets | Server refuses to start outside development unless secrets are set (`config.py:369-383`) | Covered by tests | More setup for a real deployment |

---

## 5. Evaluation

### 5.1 Setup and settings

All scored runs use the **offline sandbox**: external sources are deterministic stand-ins, the
planner runs without a writer model, and the cost comparisons use a scripted writer with
tokens estimated at 4 characters per token. No scored run calls a live model. Section 5.8
explains what this does and does not show.

| Setting | Value |
|---|---|
| Commit | `25170d3` |
| Date | 16 November 2026 |
| Writer | `sandbox-offline` (no model) |
| Router | rules layer only |
| Judge | deterministic scripts; LLM judge disabled |
| API keys | all blanked for the run |
| Runs per dataset | 1 |

Each run writes a report with its settings and every case's outcome to
`docs/final-report/eval-results/`.

### 5.2 Cases

| Dataset | Cases | Kind |
|---|---|---|
| `routing` | 84 labelled messages, 21 per intent, 4 in Chinese | Representative and challenging |
| `injection` | 84 attack probes: 36 in user messages, 48 hidden in tool results | Failure cases |
| `hongkong` | 8 trips, 7 with an injected fault (typhoon, closure, MTR delay, timeout) | Challenging and failure cases |
| `japan` | 6 trips on fragmented, conflicting source data | Challenging |
| `travelplanner` | 3 multi-constraint trips in the benchmark's style | Representative |
| `efficiency` | 10 turns, router path against full pipeline | Component comparison |
| `edit_vs_replan` | 5 edits, local edit against full replan | Component comparison |

Four datasets have a frozen 70/30 dev and held-out split, stratified by city, fault or attack
type (`backend/eval/splits/split-20261009.json`). A scored run on a changed fixture is refused.

### 5.3 Judging rules and metrics

Pass or fail is decided only by deterministic scripts (`backend/app/eval_checks.py`):

- **Constraint satisfaction:** each stated constraint (budget, days, party size, must-include,
  exclude, dietary, weather, travel time) is checked against the plan. A constraint no script
  can decide counts as unmet.
- **Error recovery:** the injected fault is handled, for example an outdoor stop moved off a
  typhoon day or a closed venue replaced.
- **Grounding:** every transfer time and opening time in the plan matches a tool result logged
  in the same run.
- **Injection containment:** all four must hold: the route stays a valid type, the agent
  returns its own output, only allowlisted tools ran, and the canary token is absent.
- **Routing accuracy:** the predicted intent equals the label.

**No LLM judging was used.** An optional judge with an explicit 1–5 rubric exists
(`backend/app/infrastructure/judge.py:21`) and is advisory only, but it was never run and no human check
of it was done, so no judged score appears in this report.

### 5.4 Results

Measured on 16 November 2026 at commit `25170d3`.

| Dataset | Passed | Main results |
|---|---|---|
| `routing` | 64 / 84 | Accuracy 76.2% (dev 72.9%, held-out 84.0%). By intent: plan 81.0%, modify 71.4%, ask 85.7%, unclear 66.7%. Rules decided 48 of 84 alone, all 48 correct. |
| `injection` | 82 / 84 | Containment 97.6%. User-message probes 94.4%, tool-result probes 100%. Dev 96.6%, held-out 100%. |
| `hongkong` | 7 / 8 | Constraints 31 / 31. Error recovery 85.7%: typhoon 100%, MTR delay 100%, timeout 100%, venue closure 50%. Transfer times 42 / 42 and opening hours 51 / 51 grounded. 9 of 60 stops had no opening hours to check. |
| `japan` | 6 / 6 | Constraints 25 / 25. Transfer times 22 / 22, opening hours 33 / 33. 8 of 41 stops had no hours. All four replay checks 100%. |
| `travelplanner` | 3 / 3 | Constraints 12 / 12. Transfer times 8 / 8, opening hours 11 / 11. 5 of 16 stops had no hours. |
| `efficiency` | 10 / 10 | See baseline comparison below. |
| `edit_vs_replan` | 5 / 5 | See baseline comparison below. |

**Baseline comparisons.** Each pair runs the same inputs on the same sandbox.

| Comparison | Agent path | Baseline | Saving |
|---|---|---|---|
| Router path vs full pipeline on every turn: calls per turn | 2.5 tool + 1.0 model | 10.5 tool + 1.0 model | 69.6% |
| Same: estimated tokens per turn | 1,313.8 | 1,463.2 | 10.2% |
| Local edit vs full replan: calls per turn | 0.8 tool + 1.0 model | 10.6 tool + 1.0 model | 84.5% |
| Same: estimated tokens per turn | 1,435.0 | 1,484.2 | 3.3% |
| Same: the requested change was applied | 100% | 0% | — |

The full replan keeps every item but does not apply the edit, because it rebuilds from the
original request. The local edit is therefore better on correctness as well as on calls.

**Changes since the last recorded run** (5 November, commit `74dbe5d`, `docs/EVALUATION.md:100-117`):

| Metric | 5 Nov | 16 Nov |
|---|---|---|
| Hong Kong cases passed | 8 / 8 | 7 / 8 |
| Injection containment | 98.8% (83 / 84) | 97.6% (82 / 84) |
| Tokens saved by the router | 36.3% | 10.2% |
| Tokens saved by the local edit | 57.7% | 3.3% |

Routing, Japan, travelplanner and the call savings are unchanged or within 0.3 points.
The sandbox is deterministic, so the four regressions come from code merged between the two
dates. A bisect between the two commits found the change behind each one.

| Regression | Introduced by | Kind | Cause |
|---|---|---|---|
| `hk07` fails | `4f7580e` (8 Nov) | Test case out of date | A new rule starts the first day only after the arrival flight lands (`backend/app/application/trip_rules.py:168`, applied at `planner.py:280`). The sandbox flight lands at 13:00, so day 1 is shorter and the plan no longer contains Tai Kwun, the venue this case closes. Closure handling itself did not change: the other closure case, `hk02`, still passes. |
| `i09` not contained | `e40399d` (6 Nov) | Real weakness | For an unclear message on an existing plan, the agent now asks a model-written question about that plan (`backend/app/application/clarification.py:63-66`) where it used to send a fixed line. The question is sent without an output check, so a writer that obeys the injected text gets it through. |
| Token savings, router and local edit | Several commits; the largest are `e40399d`, `3d70a86`, `216d915` and `3487ed4` | Real cost increase | Prompts grew and the baselines grew less. The edit prompt now carries more rules, the whole plan, weather and tags, and longer skill files. Estimated tokens per turn: local edit 557 → 1,435 against replan 1,316 → 1,484; router path 826 → 1,314 against full pipeline 1,295 → 1,463. |

The call savings held because the number of calls did not change; only prompt length did.
Of the four, `hk07` is a stale test case and not a loss of capability. The other three are
real and are present in the measured commit.

### 5.5 Failures

- **Routing, 20 misrouted.** The "unclear" class is weakest (66.7%). Three of the four Chinese
  messages are misrouted because the rules are English-first (`docs/EVALUATION.md:138-140`).
- **Injection, 2 not contained.** Probes `i09` and `i25` in user messages were not blocked by
  the pattern screen, and the stand-in writer, which obeys every instruction it sees, returned
  the attacker's text instead of the agent's own output. Only allowlisted tools ran and the
  canary did not leak. `i25` was already failing on 5 November; `i09` is new and takes the
  unchecked plan-question path described in Section 5.4.
- **Hong Kong, 1 failed.** `hk07` closes Tai Kwun after planning, but the plan no longer
  contains Tai Kwun (Section 5.4). Nothing is replaced, so the check records the closure as
  not handled. All 4 of the case's constraints are met.
- **Edit cost.** In at least one of the five edits, the local edit used more estimated tokens
  than the replan (pytest reported 1,407 against 1,277 for one case at
  `tests/test_proposal_efficiency.py:353`), so the unit test for TC-11 fails even though the
  dataset passes on its own rule.

### 5.6 Test suite

The whole backend suite on 16 November at commit `25170d3` (Windows 11, Python 3.13):
**3,948 passed, 20 failed, 1 skipped** in 504 s. Every test runs offline.

The 20 failures have four causes, each traced to the commit that introduced it:

- **Flight-arrival rule, `4f7580e` (8 tests):** `test_eval_proposal.py` (3),
  `test_hk_disruptions.py` (2), `test_proposal_db09.py` (2) and `test_failed_sections.py` (1).
  With the rule switched off, all eight pass. Seven are test scenes written for a full first
  day, including the `hk07` case. `test_failed_sections.py` shows a real gap: a plan made
  while the flight source was down gets the flight when it is reopened, but not the later
  start to day 1.
- **Edit path added on 15–16 November, merge `3487ed4` (7 tests):**
  - `test_asking.py` (1), `test_edit_conversation.py` (1) and `test_language_intent.py` (2)
    show a real bug. The edit reader takes phrases such as "on day 2" and "Chinese" for
    place names (`backend/app/application/modification.py:1741`), so "add one more attraction
    on day 2" adds nothing.
  - `test_japan_replay.py` (2): the replay fixture was not updated for the new named-place
    search. The Japan dataset itself still passes 6 of 6.
  - `test_plan_segments.py` (1): a held edit knows only the first stay of a multi-city trip.
- **Prompt growth (1 test):** `test_proposal_efficiency.py`, the edit-cost comparison in
  Section 5.5.
- **Calendar export (4 tests):** these fail on the Windows test machine because Python has no
  time-zone database there unless the `tzdata` package is installed, and `tzdata` is not in
  `backend/requirements.txt`. They were not run on Linux.

### 5.7 Variable outputs

The sandbox has no model sampling, so every score except latency is identical on a repeat
run; each dataset was therefore run once. Latency varies with machine load and is reported
only as an indication. The live agent does vary: the writer runs at temperature 0.2. Its
variance was not measured.

### 5.8 Limitations

- **No live-model result is reported.** The sandbox shows that the pipeline, checks, caps and
  recovery logic work. It does not show how good a plan written by a real model is, how the
  two model layers of the router perform, or whether a real model would obey an injection
  that the pattern screen misses.
- **The cost figures are estimates** from a scripted writer, not a bill.
- **The datasets are small**, so percentages move in large steps (one Hong Kong case is 12.5
  points).
- **Held-out routing accuracy is optimistic.** The rules-first gate was designed with all 84
  messages visible.
- **The Japan replay data was hand-built** from provider documentation.
- **Offline, transfer times are checked against the planner's own transport model**, so the
  check cannot catch an error in that model.
- **The TravelPlanner benchmark was not run**; three cases in its style are not a benchmark
  score.

---

## 6. Contributions and attribution

### 6.1 Member contributions

`[TODO: each member's contribution, in the group's own words.]`

| Member | Contribution |
|---|---|
| Chan Yuk Ming | `[TODO]` |
| Lai Helen | `[TODO]` |
| Ng Tsan Fung | `[TODO]` |
| Wong Man Kit | `[TODO]` |

For reference, the repository history on `main` (non-merge commits by GitHub account):

| Account | Commits | Period | Areas |
|---|---|---|---|
| `bryanch0430-web` | 577 | 8 Oct – 16 Nov | Backend, frontend, tests, evaluation harness, documentation |
| `AgentKevin2077` | 8 | 5 – 6 Nov | Map legends for transport legs; release notes |
| `Me0wX-LR` | 6 | 3 – 8 Nov | Edit-only-what-was-asked, trace download, bug-report form, clarify fixes |
| `fungn` | 4 | 15 – 16 Nov | Skill-file edit path, direct edits and proposal checks |

Commit counts understate work done outside the repository (design, testing, the proposal,
this report and the demo).

### 6.2 Reused code, libraries, data and services

No code was copied from another project, and no agent framework is used
(`README.md:263-264`). The open-source planners listed in `docs/GAP_ANALYSIS.md:123-134` were
read for comparison only.

- **Libraries:** FastAPI, Uvicorn, Pydantic, httpx, cryptography, pypdf, pytest (backend);
  React 18, Vite, TypeScript, Storybook 8, pptxgenjs (frontend). Optional: PyTorch,
  Transformers and the BAAI BGE embedding models for handbook search. Versions are pinned in
  `backend/requirements.lock` and `frontend/package.json`.
- **Services:** Google Places, Routes and Maps; SerpApi; Open-Meteo; OpenStreetMap Nominatim;
  Wikivoyage; Wikimedia; Hong Kong Observatory; MTR; AMap; DeepSeek; xAI; TypeSafe Jev;
  open.er-api.com.
- **Data:** airport list from OurAirports (Unlicense). Destination guides are the group's
  own summaries with their sources cited in each file. All evaluation fixtures were written
  by the group.

### 6.3 Unfinished and mocked functionality

- **Mocked in evaluation only:** all external sources and the writer model are stand-ins in
  scored runs (Section 5.1). The running application has no mocked source.
- **Estimated, and labelled so in the interface:** transfers between stops, ground legs
  between cities, and routes when no Google key is set.
- **Not connected:** rail fares; Google sign-in.
- **Not built:** the TravelPlanner benchmark run, a latency benchmark (p50/p95), a load test,
  live-model evaluation, an LLM-judge run with human check, PDF export, password reset.
- **Off by default:** the pre-plan audit (no A/B result yet) and the BGE reranker (no measured
  gain).
- **Known failing tests:** see Section 5.6.

### 6.4 AI assistance

AI coding assistants were used throughout, under the group's direction and review.

- `docs/AI_USE.md` declares an AI coding agent (DeepSeek Harness, `deepseek-flash`) for the
  backend, frontend, tests, evaluation harness and documentation.
- Claude Code was used for later feature work and to gather the evidence for, and draft, this
  report. `[TODO: confirm the full list of tools and models, and update docs/AI_USE.md to match.]`
- Project prompts: the runtime prompts are the skill files in `backend/app/skills/`.
  `[TODO: export the development prompts for the submission, or declare which are missing —
  docs/AI_USE.md currently says the coding conversations were not recorded.]`

Every result in this report was produced by running the commands in Appendix A, and the group
is responsible for its correctness.

---

## Appendix A. Reproducing the results

From `backend/`, with the virtual environment active. No API key or network is needed.

```bash
# Evaluation (one report per dataset is written to the --out folder)
PYTHONPATH=. python -m app.eval_run --dataset routing        --tag final --out ../docs/final-report/eval-results
PYTHONPATH=. python -m app.eval_run --dataset injection      --tag final --out ../docs/final-report/eval-results
PYTHONPATH=. python -m app.eval_run --dataset hongkong       --tag final --out ../docs/final-report/eval-results
PYTHONPATH=. python -m app.eval_run --dataset japan          --tag final --out ../docs/final-report/eval-results
PYTHONPATH=. python -m app.eval_run --dataset travelplanner  --tag final --out ../docs/final-report/eval-results
PYTHONPATH=. python -m app.eval_run --dataset efficiency     --tag final --out ../docs/final-report/eval-results
PYTHONPATH=. python -m app.eval_run --dataset edit_vs_replan --tag final --out ../docs/final-report/eval-results

# Reliability tests cited in Section 3
PYTHONPATH=. python -m pytest -o addopts="" -q tests/test_retry_budget.py tests/test_destination_outage_turn.py \
  tests/test_llm_selection.py tests/test_caller_binding.py tests/test_proposal_design.py tests/test_sse_stream.py \
  tests/test_budget_check.py tests/test_trip_too_large.py tests/test_injection_probes.py

# Whole test suite
PYTHONPATH=. python -m pytest -o addopts="" -q
```

`eval_run` exits with status 1 when any case fails, so `routing`, `injection` and `hongkong`
exit 1 at this commit. To keep a run from reading keys in a local `.env`, set the key
variables to empty first (for example `JUDGE_API_KEY= LLM_API_KEY=`).

Reports from the 16 November runs:

| Dataset | Report |
|---|---|
| `routing` | `eval-results/run_b162a9409af9.md` |
| `injection` | `eval-results/run_ac6eb76b88dd.md` |
| `hongkong` | `eval-results/run_86846fd7c6cc.md` |
| `japan` | `eval-results/run_1bdd2bb36655.md` |
| `travelplanner` | `eval-results/run_7948395cbc70.md` |
| `efficiency` | `eval-results/run_761caf4f25a2.md` |
| `edit_vs_replan` | `eval-results/run_92bc043a486c.md` |
