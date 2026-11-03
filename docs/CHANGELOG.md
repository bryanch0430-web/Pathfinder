# Changelog

All times are **HKT (UTC+8)**. Entries describe behaviour, not internals; the matching
commit is named for anyone who wants to read the diff.

---

## Unreleased — 2026-11-03

Hardening release: limits, expiry and a language switcher that works.

- **Developer scripts use the project's own Python.** `scripts/dev.sh` and `scripts/dev.ps1` run the backend, the tests and the evaluation in `backend/.venv` and say how to create it when it is missing. Node 20.19+ (CI uses 22) and Python 3.13 are written down in `.nvmrc`, `backend/.python-version` and the frontend's `engines`. Nothing changes for travellers. (`c3f1cb2`)
- **Trip tabs stay in sync and survive a damaged store.** A trip opened or closed in one browser window now shows up in the others without a reload, and a window no longer overwrites the other's tabs. A damaged or unreadable tab list is cleared and rewritten instead of leaving the page blank, and older saved tab lists are carried over. Scrapping a trip stops its reply before the trip is deleted. (`5890a14`)
- **Typing in Chinese, Japanese or Korean no longer sends half a message.** Pressing Enter to pick a word from the input method now just confirms the word. It sends the chat message, signs in or adds a preference only on the next Enter. (`3f725bb`)
- **Opening another trip stops the one you left.** If a reply is still being written when you open a different trip or start a new one, that reply is cancelled and can no longer appear in, or overwrite, the trip you moved to. (`9c022a5`)
- **A server outage keeps your trip and your sign-in.** If the server cannot be reached or answers with an error when the page opens, the open trip stays remembered and the page says to reload to reconnect, instead of forgetting the trip and starting a blank one. You are signed out only when the server says the sign-in is no longer valid. The message is in English, 简 and 繁. (`d92b7f0`)
- **Retry and call limits are settings.** `HTTP_RETRIES` now sets how often the flight, hotel, route, search and map-key services are retried, and `MAX_TOOL_CALLS_PER_TURN` (150) caps the lookups one request may make. A lookup refused by the cap shows as a data gap on the plan instead of running. (`f7981b4`)
- **A map outage is not reported as a missing place.** When the map service cannot be reached, guide places are no longer described as places that could not be located. The plan says they could not be checked because the service failed, marks it as worth retrying, and lists the places left out. (`9bf99d6`)
- **A repaired plan keeps every stop and its freshness.** When a plan fails its checks and is repaired, a stop can no longer be quietly dropped, and old prices and forecasts keep the time they were really fetched instead of being stamped as fresh. A repair that removes a stop is refused and the plan says which one. (`8931c30`)
- **A slow attraction search keeps what it found.** When finding places runs past the time budget, the places already located are used in the plan instead of none, and the plan lists a gap saying the search ran out of time. (`0a6bf9e`)
- **Two requests on one trip no longer overwrite each other.** Messages sent to the same trip at the same time are handled one after another, so the second change builds on the first instead of replacing it. If a version of the trip was saved by something else in the meantime, the traveller sees "Another change to this trip was still running. Please retry." rather than losing a version. (`43a06ad`)
- **A date change never shows the old dates' weather.** When a trip is moved and the forecast for the new dates cannot be fetched, the days show no forecast and the plan lists a weather gap (a timeout is reported as a timeout), instead of the change being refused or the old forecast staying on the new dates. Gaps from every source an edit re-runs now appear on the plan. (`9eddc52`)
- **A locked stop stays when the trip changes length.** Shortening or lengthening a trip rebuilds the plan, and a stop you locked or confirmed now stays locked, on the same day if that day still exists and on the last day if it does not. The plan says which stop it kept and where. (`1559f1b`)
- **Moving the dates keeps your hotel.** Changing a trip's dates now prices the hotel you already accepted for the new nights instead of swapping it for another one. A different hotel is chosen only when you ask for one or for a lower budget, and if the hotel's price for the new dates cannot be fetched, the plan says so. (`8668c4a`)
- **Build tools updated for security.** Storybook, Vite and TypeScript moved to the newest release of the same major version, and the Slides export uses a patched image reader. `npm audit` goes from 2 critical, 3 high and 8 moderate findings to 0 critical, 1 high and 9 moderate; what is left in Vite and Storybook needs a major upgrade. The Slides button still downloads a deck. (`9e5bb5a`)
- **Costly endpoints need an identity.** The evaluation runs and their reports now need the admin token, and destination suggestions and the travel handbook need a signed-in account or the browser's guest token. A trip's translation already followed the same rule as the trip itself. (`ebebc1b`)
- **A failing data source shows a fixed message.** When a map, weather or other source fails, the traveller is told "A data source failed. Your plan was not changed." instead of the source's own error page, and the same goes for an unexpected crash. A section that could not load shows that it failed, not a timeout. (`631c03c`)
- **A trip message cannot step out of its quotation marks.** The text a traveller types is now fenced the same way in the first reading step as everywhere else, and a fake closing tag typed in any capitals or spacing is removed, even when it is built out of pieces. (`73301b1`)
- **A deployment that forgets `ENVIRONMENT` is refused, not left open.** An unset `ENVIRONMENT` now means production, so the backend will not start on the repository's public signing secret and admin token. Local work sets `ENVIRONMENT=development` (the dev scripts do it) and the tests set `test`. (`f5bb22f`)
- **A trip opens only in the browser that made it.** Knowing a trip's id is no longer enough. The page sends its guest token with every request, a guest trip answers only to the token it was created with, and that token is never sent back to the page. Signing up or in takes over only the trip the traveller's own browser created. (`1ccd596`)
- **"Korea" is South Korea again.** Without a Google key, the first keyless geocoder (Open-Meteo, which indexes towns, not countries) answered a bare "Korea" with Koréa in Côte d'Ivoire, so the trip was planned in West Africa with no stops. A plain village is now only a last resort: the next geocoder is asked first.
- **Chat dates are read as written.** "18 to 23 November", "Nov 18-23", "18 Nov to 23 Nov", "18/11 to 23/11" and "18-23/11" give both ends, with short month names, written years and ranges across the new year. Numbers are read day first (18/11), month first only when that is impossible (11/18). The earliest date in a message is the trip's start, so a later constraint ("19/11 must be a free day") never moves the trip; fractions, counts and 24/7 are not dates.
- **Korean day titles are in English.** When the source has no English name for a day's first stop, the title romanizes the Korean name by the Revised Romanization rules and adds the place type in English, as Korean signage does ("Sangsodong Forest Bathing Area", "Gyeryongmun Gate"). It is a rule, not a model; a source's own English name still wins.
- **Slow agents and guide places.** An agent that overruns the pre-planning budget is cancelled with a reason, its section is marked unavailable and the plan is still returned; every guide place the attraction agent keeps is geocoded or left out, instead of only the first three.
- **Signing out sticks.** Legacy `pathfinder30.*` browser keys are migrated once and then removed, so a reload no longer signs the traveller back in.
- **Only web links open.** Links from web results, providers and the planner open only when they are http or https; anything else shows as plain text.
- **Share links expire and can be turned off.** A link lasts `SHARE_TTL_DAYS` (30). The share dialog shows the expiry and a "Turn off link" button (`DELETE /api/journeys/{id}/share`). Deleting a trip turns its links off. Links made before this release get 30 days from the upgrade.
- **Rate limits.** Sign-in and sign-up allow 10 requests a minute per IP; the chat stream and evaluation runs allow 30. Over the limit is HTTP 429 with `Retry-After`, shown as a plain message in the console.
- **The chat stream stays open and stops on time.** A keepalive comment goes out after 15 s of silence (`SSE_KEEPALIVE_SECONDS`). A turn that runs past 180 s (`TURN_DEADLINE_SECONDS`) ends with a `turn_timeout` error, and its planner work is cancelled.
- **Numbered database migrations.** Upgrades run once each, tracked in `PRAGMA user_version`; existing databases upgrade in place and keep their data.
- **EN / 简 / 繁 translate the whole console.** 394+ type-checked strings in Simplified and Traditional Chinese; a missing translation fails `npm run typecheck`.
- **Slides export.** The Slides button downloads the plan as a PowerPoint deck.
- **CI.** GitHub Actions runs the backend tests (Python 3.13) and the frontend typecheck and build (Node 22) on every push and pull request to main.
- **Every proposal test commitment has a page.** `docs/TEST_CASES.md` maps the proposal's evaluation commitments (TC-01 to TC-12) and design behaviours (DB-01 to DB-18) to their tests, status and latest result. It also lists the known gaps, what is not done and the decisions left for the team. Tests that check an ID are named after it, for example `test_tc05_...`.
- **The router is scored on 84 labelled utterances.** There are 21 per intent across Kyoto, Tokyo, Osaka and Hong Kong, 4 of them in Chinese. Accuracy is reported per split and per intent: 76.2% for the rules layer. (`8859934`)
- **Injection probes run as real turns.** 84 probes, 48 of them hidden inside web results, place text or notes, are scored on the proposal's four containment conditions: 96.4% contained. Fetched page text that addresses the model is replaced before any prompt sees it. (`1aecd3b`)
- **Frozen 70/30 split.** `python -m app.dataset_splits freeze | check` gives the router, injection, Hong Kong and Japan sets a seeded split stratified by city and constraint type. The split is recorded with a hash of each fixture in `backend/eval/splits/split-20261009.json`. (`a1270e3`, `3d9751a`, `d402d03`)
- **Plans are scored per constraint, per disruption and against the tool log.** There are now 6 Japan cases and 8 Hong Kong cases. A typhoon signal, a venue closure, an MTR delay and a source timeout are each simulated differently. A closed venue is replaced through the edit path. The typhoon and MTR delay are not handled yet and are recorded as strict xfails. The sandbox no longer fetches exchange rates online. (`5f2a897`, `0981f86`)
- **The planner keeps must-include stops and the traveller's diet** when it fills a short trip. (`c86c52e`)
- **Router and edit costs are measured.** Against always running the full pipeline, the router path makes 52.2% fewer API calls but uses 57.9% more tokens offline. Against a full replan, a local edit makes 53.3% fewer API calls, uses 27.4% fewer tokens and keeps every other item. Removing a named stop is no longer undone. (`32ef57a`, `216614a`)
- **Every tool call is logged.** Exchange-rate refreshes and agent web searches are now tool calls, and each run reports calls logged against calls made (100%). Web lookups on the plan path go through the allowlist and the text screening. (`180fbf0`, `605b8b2`)
- **The evaluation judge is checked.** It is shown the plan's real stops and cost. The run report flags a judge from the writer's own model family, and the judge's tokens count toward the run. (`00b1fed`, `ab990a2`)
- **More design behaviours are tested:** bounded plan repair ending in a retryable error (`8abe07a`); structured errors and two repair chances for malformed tool requests (`ea61f84`); fetch timestamps on forecasts, fares and stays (`aae531a`, but outdated data is not refreshed yet); a background write of preferences to long-term memory (`20b5aba`); and hotel ranking by rating and distance (`0cf5adc`).
- **Trip rules are kept, even without a model.** A day the traveller wants free stays empty, the first and last days fit the flight times, and a named place is searched for and scheduled, or reported when it cannot be found. (`557049f`)
- **Hong Kong plans check live warnings and MTR status.** Hong Kong Observatory warnings (`weather.warnings`) move outdoor stops off a typhoon day, and an MTR Next Train delay (`routes.status`) is added to that day's transfers; a locked stop never moves. Both feeds report only what is in force now, so a trip that does not include today skips them. The Hong Kong set now passes 8/8, with error recovery 100% (7/7). (`dbcb3ff`, `4c26888`, `cf8a845`, `d57c24d`)
- **Every stop and meal starts after its transfer.** Transfer times come from a logged `routes.segments` call, with any reported MTR delay, and stops stay inside the opening hours their source states. The grounding check now covers transfer times and opening hours, and counts stops whose source gives no hours. (`c7faeff`)
- **The Japan set is scored on fragmented provider responses.** Each case replays responses shaped like Google, Open-Meteo, Nominatim, Wikivoyage and DeepSeek search, with missing fields, Japanese and English names, look-alike places, conflicting coordinates and a failed request. The merge now compares names in any script, keeps each source's other names and settles coordinate conflicts by agreement. A live mode against the real providers is opt-in (`PATHFINDER_JAPAN_LIVE=1`). (`2e08681`, `a7ca414`, `b20a66e`, `1be8db2`)
- **The router costs less in calls and tokens.** The rules decide confident messages without a model call, and the router, ask and modify prompts send only what they read. Against the full pipeline the router path now makes 69.4% fewer API calls and uses 36.8% fewer tokens offline. The rules-first gate was designed with the whole routing set visible, so held-out routing accuracy is optimistic. (`5d8aaa8`)
- **An edit calls only the agents it needs.** A day or night named in an edit no longer reruns weather, hotel and flights; removing a stop or changing the pace calls no agent; exchange rates refresh only when a stay or flight can change; a flight change no longer swaps the hotel; and a pace change keeps confirmed and locked stops. Against a full replan, an edit now makes 84.2% fewer API calls and uses 57.7% fewer tokens. (`1f121a0`, `ee8400d`)
- **A question takes one model call.** No tool is called when the plan holds the answer, a question unrelated to the trip is flagged instead of answered from the plan, and a date outside the trip fetches its own forecast. (`82c408f`)
- **The injection log keeps hourly totals only.** Each category, severity and channel gets one counter per hour, with no message text or personal data; migration 3 converts old rows. A blocked message's text stays in that trip's own chat history. (`2926490`, `e94b6d1`)
- **Retries stay inside a time budget.** Timeouts, 408, 425, 429 and server errors are retried with backoff and jitter, `Retry-After` is honoured, and the total never runs past the budget. (`ac6c427`)
- **Outdated data is refreshed.** On a later turn or when a plan is reopened, an outdated forecast, fare or hotel price is flagged and fetched again by its own agent only; the plan's data panel shows what was refreshed and what is still outdated. (`35b46ab`, `e2e2005`, `f26cd30`)
- **AMap is the fallback for mainland China.** When `AMAP_KEY` is set (off by default), geocoding and place search in mainland China fall back to AMap if Google is unconfigured, failing or empty, and its GCJ-02 points are converted to WGS-84. The console map stays Google, and distances do not use AMap. (`3acfd0f`, `10a2233`, `7798b44`)
- **Test cases and evaluation refreshed.** `docs/TEST_CASES.md` and `docs/EVALUATION.md` match fresh runs at `f26cd30`: 446 tests pass, 1 is skipped (the live Japan run) and none are xfailed. Table A has 9 covered, 2 partly covered (TC-02, TC-07) and 1 not done (TC-01).

## v1.3.0 — 2026-11-03 11:55

Edit release: the assistant changes the thing that was asked for, and leaves the rest.

- **One referred item.** A name in the sentence, or the selected chip when the sentence says "this", is the only row that may change. Hotel, flights and other stops are restored.
- **A question is a question.** A message that ends in "?" and does not ask for a change is answered. A cuisine question is answered from public pages, and the reply says when those pages do not state it.
- **Pace and schedule, within limits.** "One place a day", "two stops", "no meals", "start after 14:00" and "at least 3 hours" reshape the existing days. Zero places, more than four places a day, a start outside 08:00–18:00, or a visit longer than six hours is refused. "Spend 3 hours" is time, not a cheaper hotel.
- **Web references stay references.** DeepSeek search runs first. Relevance is our own check. Grok runs only when that relevance is zero. A page never becomes a price or a temperature.
- **The map follows the selection.** Pressing a stop zooms to it. The camera is pinned on the traveller token by default, and the control under the clock turns that off.

## v1.2.0 — 2026-11-01 23:57

Clarity release: the console stops producing answers that *look* like actions but are not.

- **Identity questions answer instantly and honestly.** "Who are you?", "are you a real
  person", "which model are you" return a canned, testable reply in ~40 ms with
  `reason_code: identity` — no understand call, no router call, no web search. The reply
  never claims to be human. (`f1020c1`)
- **Clarify offers real next steps.** When the clarify is about intent rather than missing
  fields, the card lists four concrete messages (Plan my trip / Make it cheaper / What is on
  day 1? / What changed?). The Continue button no longer re-sends
  "Please continue with the current details.", which could only loop. (`dad3eaa`)
- **The source/freshness row reads as a label.** The whole `Live · google-places: …` strip
  sits on a subtle tinted band instead of looking like body text. (`f9725a4`, `3fd7672`)

## v1.1.0 — 2026-11-01 23:28

Ask release: questions the plan cannot answer are answered from real sources, and the
selected stop is actually used.

- **Web search, two sources with a policy.** DeepSeek Search (`/v1/tools/search`) answers
  interactive questions in ~0.6 s for $0.75/1,000 calls. Grok's Agent Tools search
  (`/responses` + `web_search`) synthesises across ~30 sources but takes 13–23 s, so it is
  opt-in (`GROK_SEARCH_ENABLED`) and reserved for synthesis questions. (`730ef5b`)
- **Follow-ups keep their thread.** "really?" / "why?" now receive the previous exchange, so
  the answer continues that topic instead of describing the whole itinerary. (`5ca9492`)
- **Opinion questions cite reviewers.** Google Places reviews are requested and three are
  passed to the writer, so "is it fun?" answers with what visitors praised, not just the
  editorial summary. (`a799184`)
- **The selected stop is honoured.** Place lookup and web search resolve the row selected in
  the console; a distance question with a stop selected is answered from that stop's
  coordinates. (`546c767`)
- **The question keeps its tag.** The sent message stores `meta.scope`
  ("Day 1 · Ocean Park"), rendered above the bubble and preserved across reloads.
  (`dec40a4`, `aa74cba`)
- **Distance, nearest and missing-section questions** are answered from data already held;
  a failed hotel or flight lookup now names the source error instead of "the plan has no
  hotel". (`e2fd42d`)

## v1.0.2 — 2026-11-01 22:50

Modify release: the path only reports changes it actually made.

- **"Make it cheaper" with nothing priced asks instead of pretending.** With no priced line
  in the plan it returns a question (replace the stop, or keep it), no version bump and no
  bogus gap. (`5ab24f7`)
- **Named swaps and duplicates.** "change Ocean Park to Disneyland" resolves the place after
  the last *to*, joined words resolve ("oceanpark" → Ocean Park), and a place already in the
  plan cannot be added twice: the reply names the existing day and time. (`c11f3f4`)
- **Meals are modifiable**, and a replacement can be targeted by index, name or scope.
  (`649a228`)
- **A no-op is not a version.** When the diff is empty the version stays put and the reply
  states the reason. (`649a228`)

## v1.0.1 — 2026-11-01 20:30

- **Product name is Pathfinder**, not "Pathfinder 30": README, HANDOFF, gap analysis, the
  FastAPI title, `app_name` and the regenerated OpenAPI contract.
- **Browser storage keys renamed** `pathfinder30.*` → `pathfinder.*` with a one-time
  migration, so existing sessions survive. (`eb7b9c7`)
- **Screenshots refreshed** for the current UI (14 of 18); see `docs/screenshots/README.md`.
  (`a4043c0`)

## v1.0.0 — 2026-11-01

First import of the project on branch `pathfinder-v1`: backend, frontend, evaluation
harness, contracts and documentation. (root commit)

---

## Known gaps at v1.2.0

Recorded here so the state of the system is not oversold:

- **Flights and hotels are unavailable** while the SerpApi account is out of searches
  (0 of 250). Plans show `0` priced lines and an explicit `data_error` gap; every
  budget-related measure is therefore provisional.
- **Estimates are not implemented.** The spec forbids presenting an estimate as a fare
  (`docs/SPEC.md`), so a missing price stays `unavailable`.
- **Rail is not connected**; tickets come from the flight source only.
