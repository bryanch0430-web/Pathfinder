# Pathfinder

**Group 1 · COMP7608B Final Project**  
Chan Yuk Ming · Lai Helen · Ng Tsan Fung · Wong Man Kit

Pathfinder is a conversational, state-aware agentic trip planner. A traveller describes
a trip in plain language. A typed System 1 router sends the turn down exactly one path —
**plan**, **modify** or **ask** — and anything unclear is clarified instead of guessed.
Four specialist agents gather live travel data in parallel; a grounded planner turns
verified candidates into a day-by-day itinerary where every item carries a source and a
freshness status.

This repository is the group's implementation of the Pathfinder proposal and
functional specification. The architecture, API and design system are documented
under [`docs/`](docs/); the AI-use declaration is in
[`docs/AI_USE.md`](docs/AI_USE.md).

## Version notes

Recent releases, newest first. The table is only an index. The notes under it are the patch notes. A shorter copy is also in [`docs/CHANGELOG.md`](docs/CHANGELOG.md).

| Version | When (HKT) | Headline |
|---|---|---|
| v1.3.0 | 2026-11-03 11:55 | Edits match the request; pace and hours; map pin |
| v1.2.0 | 2026-11-01 23:57 | Identity answers, useful clarify choices, label rows |
| v1.1.0 | 2026-11-01 23:28 | Web search (DeepSeek, Grok opt-in), follow-ups, reviews, scoped stop |
| v1.0.2 | 2026-11-01 22:50 | Modify only reports changes it made; named swaps; meals |
| v1.0.1 | 2026-11-01 20:30 | Product renamed to Pathfinder; storage keys migrated; screenshots refreshed |
| v1.0.0 | 2026-11-01 | First import on `pathfinder-v1` |

### v1.3.0 — 3 November 2026, 11:55 HKT

This release is about the assistant doing the change that was asked, and not a different one.

**Only the referred row changes.** If the sentence names a stop or a hotel, that name is the target. If it says "this", "switch", or "somewhere else" and a chip is selected ("About: Day 2 · Pakeeza"), the chip is the target. After the edit, every other row is put back: the hotel, the flights, the other days, and the dates. The reply says "Replaced Pakeeza Food Restaurant with …" and then "Everything else is unchanged." It no longer says only "Updated Café Bohème", which hid which row had actually moved. A selected restaurant is never turned into a hotel swap. A hotel more than 40 km from the city is not a candidate. A sentence that asks for other dates or another length ("shift the trip by a week", "make it 4 days") is about the whole trip: a name in it or a selected row does not turn it into a one-row edit, the move is a new version, and the hotel you have, with its marks, is kept and priced again.

**A question is answered, not sent back to trip setup.** A message that ends in "?" and does not use a change verb (change, swap, add, remove, …) is an ask, even if it starts with a place name. "Café Bohème TKO is for eating japanese food??" is no longer answered with "I have the trip details. Could you say what you want me to plan or change?" A cuisine question is checked against public pages. If those pages mention the cuisine, the reply quotes the page. If they do not, the reply says the pages do not state it and that Pathfinder will not guess. Asking for Japanese food as a *change* searches for a Japanese restaurant. A café whose text does not mention Japanese, sushi, ramen, or an izakaya is not substituted, and the selected restaurant stays.

**The whole plan can be reshaped, without naming a stop.** These are not single-stop edits:

- How many places a day. "One place each day", "two stops a day", and the same idea in other wording. The limit is 1 to 4. Zero places, "delete the trip", or 5 or more places a day is refused and the plan is not changed.
- Meals. "No meals", "skip lunch and dinner", "meals not required" removes meal rows. The hotel and the flights stay.
- When the day starts. "After 14:00", "after 1400", "after 2pm". Every sight is moved to that time or later. A flight is not moved. A start before 08:00 or after 18:00 is refused. "I can't wake up in the morning", with no clock, is read as 11:00.
- How long a visit lasts. "Spend at least 3 hours at one spot" stretches each sight to at least that long, up to 6 hours. "Spend 3 hours" is time. It is not a request for a cheaper hotel, which is what the word "spend" used to trigger. If the next sight would run past 21:30, that sight is removed and the reply names it. A 3-hour visit that cannot start at the requested time, because 21:30 would be passed, is refused entirely.

**Web pages are references, not prices.** When a section is thin, DeepSeek search runs once. A page counts only if the city is in the title or snippet, the title matches the question (a sight, a hotel, the weather, a flight), and the host is not a video site or a shop. The provider's own score is not used: on a Tokyo query it barely moved and ranked an advertisement first. Grok runs once, and only when no page was relevant. Its text is kept only when it includes a URL. If both time out or return 429, the gap says "Search did not answer in time" and is marked retryable. A relevant page that still does not state a fare, a nightly rate, or a temperature does not get a made-up number.

**The map.** Pressing a stop pans to it and zooms to street level (zoom 15). The camera starts pinned on the traveller token. The control under the clock reads **Pinned**; press it to leave the camera where you put it. While a stop is selected, the pin does not drag the view away.

### v1.2.0 — 1 November 2026, 23:57 HKT

The console stopped producing answers that looked like actions but were not.

- "Who are you?", "are you a real person?", and "which model are you?" are answered immediately, in about 40 ms, with no model call, no router call, and no web search. The reply does not claim to be human. The route reason is `identity`.
- When a clarification is about what to do next, rather than a missing field, the card offers four real messages: Plan my trip, Make it cheaper, What is on day 1?, What changed? The old Continue button re-sent "Please continue with the current details." and could only loop.
- The source line `Live · google-places: …` is a label. The whole strip sits on a tinted band, not in the body text of the stop.

### v1.1.0 — 1 November 2026, 23:28 HKT

Questions the itinerary cannot answer are answered from sources, and the selected stop is actually used.

- DeepSeek Search (`POST /v1/tools/search`) is the interactive web source. A measured call returned in about 0.6 s. It is billed at $0.75 per 1,000 successful calls. Grok's Agent Tools search synthesises across many pages but took 13–23 s, so it is off unless `GROK_SEARCH_ENABLED` is set, and then only for synthesis questions.
- "really?" and "why?" receive the previous exchange. They continue that topic instead of describing the whole itinerary again.
- "Is it fun?" uses Google Places reviews. Up to three reviews are given to the writer, so the answer is what visitors wrote, not only the one-line editorial summary.
- Place lookup, web search, and a distance question use the row selected in the console. The distance is that stop's coordinates, not the city centre.
- The sent message keeps `meta.scope`, for example "Day 1 · Ocean Park". The tag is drawn above the bubble and survives a reload.
- "How far is this?", "what is nearest?", and "why is there no hotel?" are answered from data already on the plan. A failed hotel or flight lookup names the source error. It does not say only "the plan has no hotel".

### v1.0.2 — 1 November 2026, 22:50 HKT

Modify reports a change only when one was made.

- "Make it cheaper", when nothing in the plan has a price, asks whether to replace the stop or keep it. It does not bump the version and it does not invent a "no cheaper hotel" gap.
- "Change Ocean Park to Disneyland" takes the place after the last "to". Joined words resolve: "oceanpark" is Ocean Park. If that place is already on another day, it is not added twice. The reply names the existing day and time.
- A meal can be replaced, by its number in the day, by its name, or by the selected chip.
- An edit that changes nothing does not create a new version. The reply states why.

### v1.0.1 — 1 November 2026, 20:30 HKT

- The product name is Pathfinder. The old "Pathfinder 30" title was removed from the README, the handoff note, the gap analysis, the API title, `app_name`, and the OpenAPI contract.
- Browser storage keys moved from `pathfinder30.*` to `pathfinder.*`, with a one-time copy, so a session already in the browser is kept.
- Fourteen of the eighteen screenshots were retaken for the UI as it stood that evening.

### v1.0.0 — 1 November 2026

First import on branch `pathfinder-v1`. The tree is the FastAPI backend, the React console, the evaluation harness, the OpenAPI contract, and the docs. One path per message: plan, modify, or ask. Four agents gather places, hotels, weather, and flights. A missing source is a gap, not a guessed price.

Known gaps at v1.3.0: flight and hotel sources are unavailable while the SerpApi account is
out of searches, so budget figures are provisional; price estimates are deliberately not
implemented (see `docs/SPEC.md`); rail is not connected.

A 3-day Hong Kong plan from Singapore. The middle column is the sourced itinerary. The map on the right is pinned to the traveller (**Pinned**, under the clock) and draws the day's metro legs. Press a stop and the map zooms to that place; press **Pinned** again to let the camera stay where you leave it.

![Planner with a live itinerary, map pinned on the traveller](docs/screenshots/app_plan.png)

| Selected stop, zoomed on the map | Evaluation | Storybook Pathfinder |
|---|---|---|
| ![Tian Tan Buddha selected; the map has zoomed to the stop](docs/screenshots/app_selected.png) | ![Evaluation dashboard](docs/screenshots/app_evaluation.png) | ![Storybook planner](docs/screenshots/sb_planner.png) |

| Trip details open as a drawer | Changes called out in green |
|---|---|
| ![Trip details drawer](docs/screenshots/app_trip_details.png) | ![Updated itinerary](docs/screenshots/app_updated.png) |

| Trending rail | Traveller token on a leg |
|---|---|
| ![Trending rail](docs/screenshots/app_trending.png) | ![The map token, with the pin control and playback speed](docs/screenshots/map_token_walk.png) |

| Budget FX monitor | Map speed and moving token |
|---|---|
| ![FX monitor](docs/screenshots/fx_monitor.png) | ![Map speed](docs/screenshots/map_speed.png) |

| Anonymous profile and tag-style preferences | Elongated middle column, rounded right column |
|---|---|
| ![Profile](docs/screenshots/app_profile.png) | ![Fixed layout](docs/screenshots/layout_fixed.png) |

| Signed-in account | Marked useful → saved preferences |
|---|---|
| ![Signed in](docs/screenshots/app_auth.png) | ![Useful](docs/screenshots/app_feedback_top.png) |

| Log in / create account | Local guide with citations |
|---|---|
| ![Login](docs/screenshots/app_login.png) | ![Local guide](docs/screenshots/app_guides.png) |

| Account menu | Signed-in profile |
|---|---|
| ![Account menu](docs/screenshots/app_account_menu.png) | ![Account profile](docs/screenshots/app_profile.png) |

| Multi-plan tabs | Useful/not-useful at top right |
|---|---|
| ![Plan tabs](docs/screenshots/app_plan_tabs.png) | ![Feedback](docs/screenshots/app_feedback_top.png) |

---

## 1. What is inside

| Layer | Stack | What it does |
|---|---|---|
| **Storybook Pathfinder** | React 18 + TypeScript + Storybook 8 | The design system built first: tokens, atoms, elements, molecules, organisms, templates. |
| **Frontend** | React 18 + Vite + TypeScript + Google Maps | The console, composed only from Storybook components. English, Simplified Chinese and Traditional Chinese UI. Default currency **HKD**; budget FX monitor with live/static conversion. |
| **Backend** | FastAPI + Pydantic v2 + SQLite | Typed routing, four concurrent agents, grounded planning, bounded repair, SSE streaming, memory, evaluation. |
| **Contracts** | `contracts/openapi.json`, `contracts/trip_plan.schema.json` | The only backend ↔ frontend coupling. Regenerate with `python -m app.export_contracts`. |

There is no agent framework (no LangChain / LlamaIndex / CrewAI). Prompts, tool calls,
retries, budgets and validation are our own code.

### The one invariant

> The planner may **select and order** verified candidates. It may never **create** one.

An item without a source URL is rejected by the domain model. A model repair may not
introduce a new place or a new URL, and may only change times, stay dates and wording: every
price, coordinate, source, forecast and data gap in a repaired plan is the original's. If a
source is missing, the section is `unavailable` and the gap is shown.

---

## 2. Run it

### 2.1 Prerequisites

- Python 3.13 in `backend/.venv` (`backend/.python-version`; the code needs 3.11+)
- Node 20.19+ (CI uses 22, see `.nvmrc`) and npm
- Reference versions: FastAPI 0.141, Pydantic 2.11, httpx 0.28, uvicorn 0.47,
  React 18.3, TypeScript 5.6, Vite 5.4, Storybook 8.6.

### 2.2 Configure

```bash
cp .env.example .env
```

Fill the keys you have. Nothing is mandatory: with no keys the backend still starts,
the four agents mark their sections unavailable and the planner returns an honest,
source-grounded plan with visible gaps. `.env` is git-ignored and is not part of this
repository; create it from `.env.example` on each machine.

`ENVIRONMENT` defaults to `production` when it is not set. Local work uses
`ENVIRONMENT=development` (`scripts/dev.sh` and `scripts/dev.ps1` set it, and `.env.example`
has it) and the tests use `ENVIRONMENT=test`; the built-in `AUTH_SECRET` and `ADMIN_TOKEN`
defaults are accepted only there. For any other value, and when it is not set, the backend
refuses to start until `AUTH_SECRET` (a random value of at least 32 characters) and
`ADMIN_TOKEN` (not `local-admin` or `REPLACE_ME`) are set to real secrets. Generate one
with `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

The writer is OpenAI-compatible. The reference configuration uses xAI
`grok-4.20-0309-non-reasoning` — the low-latency general model measured for interactive
turns. DeepSeek, xAI or any chat-completions endpoint works by changing
`LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`.

`WIKIMEDIA_USER_AGENT` is preset with a contact URL and framework name. Wikimedia refuses
a generic crawler string with HTTP 403; keep this value descriptive if you fork the
project.

### 2.3 Backend (port 8100)

```bash
python -m venv backend/.venv
# Windows: backend\.venv\Scripts\activate
# POSIX:   source backend/.venv/bin/activate
pip install -r backend/requirements.txt
# optional local hybrid RAG (BGE-small + optional BGE reranker)
pip install -r backend/requirements-rag.txt

PYTHONPATH=backend python -m uvicorn app.main:app --port 8100
```

`scripts/dev.sh` and `scripts/dev.ps1` run the backend, the tests and the evaluation with
`backend/.venv` and stop with the line above if it does not exist yet.

Windows PowerShell:

```powershell
$env:PYTHONPATH = "backend"
python -m uvicorn app.main:app --port 8100
```

Check: <http://127.0.0.1:8100/api/ready> · Swagger: <http://127.0.0.1:8100/docs>

### 2.4 Frontend (port 5173)

```bash
cd frontend
npm install
npm run dev
```

Vite proxies `/api` to `http://127.0.0.1:8100`; override with `VITE_API_TARGET`.

For a single-origin build:

```bash
cd frontend
npm run build          # writes frontend/dist
```

The backend serves `frontend/dist` automatically when it exists, so
<http://127.0.0.1:8100> is the whole application.

### 2.5 Storybook Pathfinder (port 6006)

```bash
cd frontend
npm run storybook          # design-system workbench
npm run build-storybook    # static build in frontend/storybook-static
```

The Storybook is the contract for the frontend. It was built before any application
screen; the app imports the same components rather than restyling them.

### 2.6 Demo accounts

Local-only accounts created for testing:

| Email | Password | Name |
|---|---|---|
| `testaccount1@pathfinder.test` | `Pathfinder123!` | Test Account 1 |
| `testaccount2@pathfinder.test` | `Pathfinder123!` | Test Account 2 |
| `testaccount3@pathfinder.test` | `Pathfinder123!` | Test Account 3 |

Any valid email address — including a Gmail address — can register with the normal
email/password form. **Sign in with Google** is a separate OAuth integration; it needs a
Google OAuth client ID and is not enabled yet.

They are seeded through the normal registration endpoint. Change `AUTH_SECRET`, use real
passwords, and remove these rows before any public deployment.

---

## 3. The turn pipeline

```
POST /api/turn/stream (SSE)
  screen (injection flags, aggregate log)
  → understand (rules first; model fills only missing slots)
  → route (typed: intent, confidence, need_clarification, reason, affected_parts)
  → gate (threshold 0.8; missing slots; plan required for modify)
  → ONE path:

  plan    memory recall → 4 agents in parallel under a deadline
          → planner (selects/orders candidate ids) → validators → ≤2 repairs
  modify  typed change → only affected agents → merge preserving locked/confirmed
          → recalculate cost/constraints → version + 1
  ask     answer from the current plan first → else at most one allowlisted tool
  clarify one short question about the missing slots; no tools, no planning
```

### The four agents

| Agent | Owns | Fallback |
|---|---|---|
| **Attraction** | Places, coordinates, ratings, hours, dining candidates, community notes | Google Places → AMap (mainland China, only with `AMAP_KEY`) → Nominatim OSM → Wikivoyage; `unavailable` if none |
| **Hotel** | Candidates with price, rating, distance to centre | SerpApi Google Hotels → Wikivoyage Sleep; `unavailable` |
| **Weather** | Daily forecast for the exact trip dates | Open-Meteo; dates beyond the horizon become `unavailable`, never a fabricated temperature |
| **Ticket** | Flights with reference fares; reservation flags | SerpApi Google Flights; rail is explicitly not connected |

All four run concurrently. A slow or failing agent marks only its section
`unavailable`; the turn still returns inside `PREPLAN_BUDGET_SECONDS`. An attraction agent
cut off by that budget keeps what it had already found (located places, restaurants, notes,
the places the traveller named) and the section reads `partial`, provided it had located at least
one place; with none located the restaurants and notes are not kept either and the section reads
`unavailable`.

### How a day is laid out

The planner chooses *which* verified stops belong to a day; the scheduler owns the clock:

- **Stops are chronological.** Meals sit at their real time (lunch 12:30, dinner 19:00),
  never appended after the afternoon.
- **Transfers are mode-aware.** Each leg is classified by distance — walk under 2.5 km,
  metro/bus under 35 km, car under 160 km, otherwise flight — and the next stop starts
  after that ride plus a pace buffer. The itinerary note names the mode, matching the
  map legend; nothing claims a multi-kilometre walk.
- **The map follows the real roads.** `GET /api/journeys/{id}/segments` returns the provider's
  route geometry for every leg of a day — **hotel → first stop, every stop → stop
  (restaurants included), last stop → hotel** — and the console draws those polylines and
  moves the traveller along them. A straight dashed line appears only when the provider
  returns nothing for that pair.
- **Pace shapes the day.** `slow pace` → at most 3 stops, 2 h each, 15 min of slack;
  balanced → 4 stops; fast → 5. Empty days are rebalanced so a day is not left with one
  stop while another has three.
- **Early slots are data-driven, not site-specific.** The first stop opens the day at
  08:00 when the traveller named it (`I want Fushimi Inari` → `must-see:` preference), when
  the source says it opens by 08:30, when its review count marks it as a place crowds
  build at, or when four stops have to fit. The same rule works in any city.
- **No repeat stops.** Near-duplicate candidates ("Arashiyama" vs "Arashiyama Bamboo
  Forest") are collapsed, while genuinely different places ("Kyoto National Museum" vs
  "Kyoto Railway Museum") are kept.

### Security boundary

- User text is fenced as data; it never becomes an instruction. An earlier turn, or a line kept
  from one (a preference, a constraint), that a prompt repeats loses any fence tag the same way,
  and a turn the blocklist blocked or flagged high is not repeated (one flagged medium, such as "a
  hotel with no restrictions on pets", is). A tag is found however it is spelled (hidden
  characters, look-alike brackets, pieces of one inside another) in time proportional to the
  text; a text of more than 50,000 characters loses every opening bracket instead. The plan's own
  names (a destination typed in the trip form, a stop from a place source) and the router model's
  reason code lose any fence tag too where a prompt carries them.
- A blocklist screens prompt overrides, hidden-instruction requests and smuggled tool
  calls, in the message as typed and as the model will read it (a fence tag typed inside a word
  is taken out first, and a line break counts as a space, as it does when a later prompt repeats
  the turn). It aims at the assistant's own instructions and token, so a traveller's correction
  ("ignore my previous instructions about the hotel"), a reply language ("from now on, every
  reply in Cantonese"), a part of the plan dropped ("stop planning so many temples and add a
  cafe instead") and the Canary Islands or Canary Wharf pass. Suspected injections are logged as
  **category + count only**.
- Each path has an allowlist (`PATH_ALLOWLIST` in `application/toolbox.py`). `ask` can
  never run a pre-planning agent.
- A canary token is planted in every system prompt; any output containing it is blocked.
- The agent never books, pays, cancels or reads an account.

---

## 4. Clean architecture

```
backend/app/
├── domain/          entities, enums, errors, provider + repository ports
├── application/     use cases: understand, route, agents, planner, modify, ask,
│                    clarify, memory, validation, evaluation, orchestrator
├── infrastructure/  OpenAI-compatible writer, Jev, data providers, SQLite,
│                    tracing, FX, prompt loading
├── interface/       FastAPI routes, SSE, request/response models
├── security/        blocklist, fence, canary
└── container.py     composition root; the only place that wires concretes
```

Rules enforced by the structure:

- `application/` imports `domain/` only; it never imports FastAPI, SQLite or a vendor SDK.
- `infrastructure/` implements the domain ports; replacing a source is one class plus one
  registration line in `providers/registry.py`.
- `interface/` contains no business logic; it maps HTTP/SSE to the orchestrator.
- Every external call goes through `Toolbox`, which enforces the path allowlist and logs
  the call.
- One turn is one trace: JSONL in `data/traces/` and Langfuse when configured.

Details: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The A-grade gap analysis against
comparable GitHub projects (RAG, reranking, route optimization, sharing) is in
[`docs/GAP_ANALYSIS.md`](docs/GAP_ANALYSIS.md).

---

## 5. Data and truthfulness

| Fact | Source | Truth label |
|---|---|---|
| Attractions, ratings, hours | Google Places (New) | live when key works; `unavailable` otherwise |
| Attraction fallback | AMap in mainland China (`AMAP_KEY`, off by default; GCJ-02 converted to WGS-84) → Nominatim OSM → Wikivoyage (compliant Wikimedia User-Agent) | guide/OSM listings; no live hours |
| Hotels | SerpApi Google Hotels | **reference** nightly rate, availability not confirmed |
| Hotel fallback | Wikivoyage Sleep | guide listing, reference price |
| Flights | SerpApi Google Flights | **reference** fare for the travelling party |
| Weather | Open-Meteo | forecast, 16-day horizon |
| Weather beyond horizon | Open-Meteo Archive | 3-year seasonal reference, marked `stale`, never a forecast |
| Notes | YouTube Data API, Bilibili | community, last 24 months, lower trust |
| FX | open.er-api.com (12 h cache) | live reference + dated static fallback; HKD default |
| Map base | Google Maps everywhere; geocoding and place search fall back to AMap in mainland China when `AMAP_KEY` is set | browser key served by `/api/runtime`; contextual, not navigation |
| Map time | plan item `start`/`end` | 06:00–23:00 scrubber, play/pause, 1×/2×/4× speed, current-stop HUD |
| Map route key | per-leg distance estimate | travelled/ahead line style, mode colours, leg list |
| New trip / export | session + plan | scrap the old session and local pointer; Markdown export |
| Map token mode | distance estimate from verified coordinates | walk / metro / drive / flight, labelled `est` |
| Trending | trips marked useful, retrieved by hybrid BM25 + dense search | destination, days, year and sight names only |
| Profile / memory | random browser token or email account | preferences, saved-trip count, recent destinations |
| Auth | email + password + HS256 token | optional; guest mode still works |
| Plan tabs | localStorage session list | up to several open trips; switch without regenerating |
| Feedback | top-right of the itinerary | 1–5 stars, useful/not-useful, green saved animation |
| Shared trip | signed-in or guest plan | read-only page; budget, party size and session id redacted |
| Local guide RAG | 10 versioned documents (HK, Japan, USA) + citations | never used for price, hours or booking facts |
| Route order | open-path 2-opt on the chosen day stops | synthetic benchmark: avg 91.7 km → 61.7 km (−32.7%) |
| Useful → preferences | feedback on a plan | tags derived from structured choices; green save animation |
| Map route | Google Routes | day route between sights; straight-line estimate fallback |

A gap is a **statement about the current plan**, not an append-only log. New versions
drop resolved gaps; duplicate/obsolete edit gaps are removed on reload. The common cases:

- `weather`: dates outside the 16-day forecast fall back to a three-year archive average,
  labelled `Seasonal`/`stale`. If the archive also has no rows, the gap names both dates
  and both windows. A forecast source that failed (a timeout, a server error) keeps its gap
  while only the seasonal average fills the dates. A source that answers for only some of the
  trip's days leaves a gap that says how many.
- `tickets`: a departure city is now a required planning slot, so the assistant asks for
  it instead of leaving a ticket gap. Same-city trips skip flights with a note, not a gap.
  A round trip uses SerpApi's two-step flow (`departure_token`): the outbound and the
  inbound leg are shown on one option with one combined fare, never a departure alone.
- `attractions`: only a resolver failure that leaves no attraction is a gap. A failed
  higher-priority source with a working fallback (Google → Nominatim OSM → Wikivoyage)
  is not surfaced as a gap. A guide place the geocoder could not be asked about is a gap that
  names the failure (timeout, network, rate limit, auth) and says whether asking again could help; a
  geocoder that answers with a quota or key problem (Google does so with HTTP 200 and a `status`)
  has failed, it has not said the place does not exist, and neither has one whose answer has no
  readable coordinates (a `parse` failure, and the next geocoder is still asked); after two failed lookups in a row the agent
  stops asking. A guide place that is in the plan after all (the traveller named it and a place
  source located it) is not reported as left out or unchecked. A place the traveller named whose lookup
  failed is a gap too, and its "Include" check says the lookup failed. A map outage at the
  destination itself is a failed data source, not a name to rephrase.
- any section: a lookup the per-turn cap (`MAX_TOOL_CALLS_PER_TURN`) refused is a `blocked`
  gap, "Not looked up: this request reached its limit of data lookups.", and transfer times
  that fell back to estimates for that reason are a `transfers` gap. A destination that cannot
  be looked up for that reason ends the turn with a not-retryable `plan_invalid`.

What the system deliberately does **not** do: invent a price, an opening hour, a flight
number or a restaurant. It also does not book. Unavailable sections stay visible.

---

## 6. Evaluation

```bash
cd backend
PYTHONPATH=. python -m app.eval_run --dataset hongkong --tag baseline-v1
PYTHONPATH=. python -m app.eval_run --dataset hongkong --tag improved-v2
PYTHONPATH=. python -m app.eval_run --dataset routing  --tag baseline-v1
PYTHONPATH=. python -m app.eval_run --dataset injection --tag baseline-v1
```

The harness runs the **real application pipeline** against a deterministic sandbox
provider registry, so a scored run needs no network and no model key. Datasets are
frozen before scoring; an unfrozen dataset is refused.

The soft-guide corpus is ten versioned Markdown documents covering Hong Kong,
Japan and the USA. Each has a citation, licence, `last_verified` date, content
hash and corpus version. Details and the quarterly update policy:
[`docs/RAG_CORPUS.md`](docs/RAG_CORPUS.md).

```bash
cd backend && PYTHONPATH=. python -m app.guide_eval
# {'cases': 30, 'top1': 1.0, 'mrr': 1.0, 'cross_destination_noise': 0}

PYTHONPATH=. python -m app.guide_ablation
# lexical 0.9167 Top1 → BM25/dense/hybrid 1.0;
# BGE reranker 1.0 but 263.9 ms vs 10.8 ms hybrid — no gain on this corpus
```

The default guide path is **BM25 + BGE-small hybrid**; the BGE cross-encoder is
opt-in (`/api/handbook?...&rerank=true`) and stays off until a larger, harder
corpus shows a gain. Jev is still the System 1 router, not a reranker.

```bash
cd backend && PYTHONPATH=. python -m app.route_bench
# avg_before_km 91.681 → avg_after_km 61.696 (−32.71%) on 40 synthetic 6-stop days
```

The route pass is open-path 2-opt over the day's chosen stops; the benchmark is synthetic,
not live-traffic evidence. Markdown/CSV export are built into the **Evaluation** tab.
Reports land in
`data/eval/<run_id>.md`.

The evaluation tab needs at least one run; after the two `hongkong` commands above it
shows a real comparison and a trend line. Full method and current results:
[`docs/EVALUATION.md`](docs/EVALUATION.md).

The tab, like the threat list on the Settings page, calls the admin routes, so the page must
send your `ADMIN_TOKEN`. It reads the token from the browser's local storage; nothing in the
page sets it. Store it once per browser (a profile only you use, because local storage is not
a secret store) from the developer console:

```js
localStorage.setItem("pathfinder.admin", "<your ADMIN_TOKEN>")
```

Without it the tab sends no request and shows "Admin token required", in the page's language.

---

## 7. Tests

```bash
cd backend
PYTHONPATH=. python -m pytest
```

The suite needs no network and no API keys. A few tests try the keyless FX lookup and fall
back to the static rate table when it is unreachable. It covers: injection
screen/fence/canary, typed routing and the confidence gate, grounded planner composition,
minimal hotel edit with preserved stops, allowlist enforcement, memory cutoff, the
evaluation harness, accounts and per-profile privacy, and the HTTP/SSE surface.

```bash
cd frontend
npm run typecheck
npm run build
npm run build-storybook
```

GitHub Actions (`.github/workflows/ci.yml`) runs the backend tests (Python 3.13) and the
frontend typecheck and build (Node 22) on every push and pull request to `main`; the
Storybook build is not part of CI. Live end-to-end turns were exercised against the real
APIs (plan, ask, hotel swap) and captured in `docs/screenshots/`.

---

## 8. Demo walkthrough

See [`docs/DEMO.md`](docs/DEMO.md). The two-minute path:

1. **Plan** — fill Kyoto, 10–13 Nov 2026, 2 travellers, 400,000 JPY → Generate.
   Watch the four agents, then the itinerary, map, sources and budget.
2. **Select** a stop → the map pin, place card and scope chip follow.
3. **Ask** “What is the weather on day 2?” → answered from the plan, no re-planning.
4. **Modify** “Swap the hotel for the other option.” → only the hotel agent reruns;
   version becomes 2; every stop is preserved.
5. **Evaluation** → run `hongkong` twice, compare metrics, open a failed case, export.
6. **Settings** → thresholds, freshness windows, provider chains, aggregate injection log.

---

## 9. Known limitations (declared, not hidden)

- **No booking and no live inventory.** Hotel/flight prices are reference figures. The
  user completes the purchase.
- **A budget request is an honest search, not a guarantee.** "Cheaper"/"optimise the
  budget" re-runs the hotel and flight agents, keeps the cheapest verified combination,
  and — when it is still over — states the cheapest total, how far over it is, and which
  line dominates (usually flights). It never deletes confirmed stops or invents a price.
  A round-trip flight search costs up to four SerpApi calls (one outbound list plus one
  `departure_token` call per shown option).
- **Rail is not connected to a live schedule.** The ticket agent says so; it does not
  estimate a fare.
- **Weather beyond ~16 days** is not a forecast. It is filled from a three-year
  archive average, explicitly labelled `Seasonal` and `stale`; if the archive also has
  nothing, the gap explains the date range and both failed windows.
- **Long-term memory is SQLite + a deterministic hashed embedding**, not pgvector. It is
  a deliberate single-node trade-off; weak matches are excluded by the cutoff.
- **The map is contextual, not a navigation map.** Google Maps is the single base map
  everywhere, including Hong Kong and mainland China. The day route uses the routes
  provider's real geometry (plus the hotel legs and restaurant stops) and only falls back
  to a straight line when no route comes back. Playback follows the vehicle and rolls into
  the next day on its own. Tile requests need network; if the base fails, pins and route
  still render on paper. One browser key (`GOOGLE_MAPS_API_KEY`) is served by
  `/api/runtime`; the backend never calls a map vendor.
- **Transport modes on the map are estimates** from straight-line distance, not live
  routing. The token switches walk/metro/drive/flight and every leg labels `est`;
  flight appears on long intercity legs (>160 km), so a single-city plan normally shows
  walk, metro and drive.
- **Trending is workspace-local and retrieval-based.** Only trips marked useful are
  indexed; a query, the current destination and the caller's own preference tags are run
  through BM25 + a dense vector, fused with reciprocal rank fusion, then aggregated into
  cards. It exposes no owner, budget, party size, session id or other traveller's
  preferences. Seed a platform-sized corpus with
  `backend/scripts/seed_platform_demo.py`; see `docs/TRENDING_RAG.md`.
- **Profile works logged-out or logged-in.** Guests use a random browser token; email
  accounts are optional and take over the guest's preferences and saved trips on first
  login. Saved trips, preferences and planner recall are per profile: nobody sees or
  recalls another traveller's. A profile keeps at most 20 preferences of at most 80
  characters: the Settings page refuses more, and what turns, feedback and sign-in learn is
  cut to fit, keeping the newest. A trip's form keeps at most 50 preferences and 50 constraints of at
  most 200 characters (120 for its other texts): saving more is refused, and what a message adds is cut
  the same way. Clearing memory removes only the caller's saved trips and
  preferences; clearing chat removes messages but keeps the current plan. **New trip** deletes the current session row and local pointer, so a stale
  `pathfinder.session` cannot pin the console to an old plan.
- **Export** writes Markdown from structured fields and sources, not a model transcript.
- **Local-guide RAG is small and needs the optional packages.** It is destination-filtered
  and cited, and the eval set reports Top1/MRR/noise. The default BM25 + BGE-small hybrid
  needs `backend/requirements-rag.txt`; without it the retriever falls back to lexical
  matching and says so. The BGE reranker is opt-in and showed no gain on this corpus, and
  Jev is not used as a reranker.
- **Share links are public until they expire.** Redaction removes budget, party size,
  session id and client token. A link stops working after `SHARE_TTL_DAYS` (30), when the
  owner turns it off in the share dialog, or when the trip is deleted; comments remain roadmap.
- **Rate limits are per process and per IP.** Sign-in and sign-up allow 10 requests a
  minute, planning turns and evaluation runs 30, new trips and share links 60, place
  suggestions 60 and handbook look-ups 60, each with its own budget (`RATE_LIMIT_*_PER_MINUTE`). Behind a
  reverse proxy, start uvicorn with `--forwarded-allow-ips` for that proxy, or every user
  shares its address. The application never reads `X-Forwarded-For`, but uvicorn does: for a
  peer in `--forwarded-allow-ips` (default `127.0.0.1,::1`) it takes the right-most address in
  the header that is not itself trusted, so the proxy must set or append the header. Never use
  `*` (or `FORWARDED_ALLOW_IPS=*`) there, behind a proxy or not: uvicorn then trusts every peer
  and takes the left-most address, which the caller chose, so every caller picks its own address,
  and so its own budget. List the proxy's address or network instead. `--no-proxy-headers`
  limits by the socket peer only.
- **Auth has no password reset or email verification yet.** Passwords use scrypt and tokens
  are HS256 JWTs; change `AUTH_SECRET` outside a local demo.
- **Origin is required for planning.** It is a gate slot, so a plan without a departure
  city asks for it instead of silently skipping tickets. A message sets it only when it says so:
  after a travel word right before "from" ("flying from", "leaving from", "flights from"), in
  "a trip from X to Y", in a statement ("my departure city is X", "change the origin to X") or
  in an explicit change ("actually, plan it from X", "start from X instead"). These replace the
  departure city a trip has. A planning request ("plan Kyoto from X"), a stay ("a week in
  Paris from X") or a bare "from X" only gives a trip that has none a city the assistant
  knows, and a question never does. "From scratch", a weekday, "home", an airline, a station
  ("how far is the temple from Kyoto Station?"), "a day trip from Kyoto to Nara" and "from
  the list above" never name one, and the name ends at "and", "via", "at" or "around" ("flying
  from Leeds via Dubai" is Leeds). Any other city is read by the writer model when one is
  configured, else asked for.
- **Evaluation runs are sandboxed** for determinism. They exercise the real pipeline,
  not live providers; live behaviour is captured separately in traces and screenshots.
- **Chinese covers the interface, not the plan text.** The EN/简/繁 switcher translates
  every label, button and message in the console. Prompts, quick-prompt chips, plan
  narrative and traces stay English-first, because the planner reads that text.
- **Admin endpoints use one shared token.** They require an `X-Admin-Token` header equal to
  `ADMIN_TOKEN`. Outside `ENVIRONMENT=development` or `test` the backend refuses to start
  with the default or placeholder token (see section 2.2); set a real one before exposing
  the process. The web page has no field for it: section 6 shows how to store it in the
  browser for the Evaluation tab.
- **Policy thresholds are environment-configured.** The Settings screen is intentionally
  read-only; changing a threshold means editing `.env`, restarting and recording the diff.

---

## 10. Repository map

```
Pathfinder/
├── .github/workflows/       CI: backend tests, frontend typecheck and build
├── backend/
│   ├── app/                 layered application (domain → application → infrastructure → interface)
│   ├── tests/               offline pytest suite
│   └── eval/fixtures/       routing, injection, HK, Japan, TravelPlanner fixtures
├── frontend/
│   ├── src/design-system/   Storybook Pathfinder: tokens, components, stories, fixtures
│   ├── src/app/             application pages
│   ├── src/lib/             API client, session hook, i18n
│   └── .storybook/          Storybook configuration
├── contracts/               OpenAPI + TripPlan JSON Schema
├── docs/                    spec, architecture, API, design, evaluation, demo
├── samples/                 ordinary, changed and failure-case inputs
├── scripts/                 dev helpers
├── .env.example
└── README.md
```

---

## 11. Non-functional targets

| Target | Value | How it is enforced |
|---|---|---|
| Turn latency | interactive: typical live plan 12–20 s | fast writer model; agents parallel; budget 90 s |
| Agent deadline | 30 s each | `asyncio.wait_for` + overall `asyncio.wait` |
| Repair bound | 2 | `MAX_REPAIRS`; then a typed error, never a dirty plan |
| Routing gate | 0.8 | configurable; below it the turn clarifies |
| Memory cutoff | 0.72 | configurable; weak matches are not injected |
| Retries | 2 per request | `HTTP_RETRIES`, inside twice the request timeout; it covers the main data sources (flights, hotels, routes, places, forecast, geocoding, web search). Elevation, time zones, videos, Hong Kong feeds, archive weather and exchange rates keep a fixed count of their own |
| Tool-call cap | 150 per tool path | `MAX_TOOL_CALLS_PER_TURN`; the plan, an edit, a question and the refresh of an outdated plan each count their own, so one request can make up to twice the cap. A refused lookup shows as a data gap; 0 turns the cap off |
| Freshness | weather 6 h, tickets 24 h, hotels 7 d, attractions 30 d | timestamps on every item |
| Privacy | no identity data in long-term memory | schema + aggregate injection log |
| Observability | one trace per turn | JSONL always; Langfuse when keys are set (an exception's text stays in the JSONL, Langfuse gets its class) |

---

## 12. AI use

AI-use declaration: [`docs/AI_USE.md`](docs/AI_USE.md).  
Functional baseline: [`docs/SPEC.md`](docs/SPEC.md).

Credentials are not part of the submission. `.env` is ignored; `.env.example` documents
every variable. Regenerate contracts and screenshots rather than committing secrets.
