# Multi-City Trips: Handoff Plan for Steps 2-7

This document is written for an agent that has not seen this project or the conversation that produced it. Follow it in order. Where it gives a name, a signature or a number, use exactly that. Where it says "read first", read those lines before writing anything for that task.

**Spec (binding):** `docs/superpowers/specs/2026-11-12-multi-city-trips-design.md`. Read all of it once before starting. If this plan and the spec disagree, the spec wins; write the conflict in your report.

**Step 2 detail:** `docs/superpowers/plans/2026-11-12-segments-stays-legs-model.md`. Steps 3-7 are below.

---

## 0. Rules for the whole job

1. **Target:** repo `C:\Users\Bryan\Documents\PathFinder`, branch `multi-city-trips`. Never commit to `main`. Never push, merge or open a PR.
2. **One step at a time, in order (2, 3, 4, 5, 6, 7).** A step is finished only when its exit checklist (section 9) passes. Do not start the next step with a red checklist.
3. **Test first.** For every task: write the test, run it and see it fail for the right reason, write the code, see it pass. A test that passes before the code exists is wrong; fix the test.
4. **Single-city trips must not change.** After every task, a trip with one city must give the same plan, replies and API output as before, apart from new fields with default values. If an existing test fails, your change is wrong; do not edit the existing test to make it pass. The only allowed exceptions are listed under each step as "Expected test changes".
5. **Known failing tests (do not fix, do not count):** these 9 fail on `main` already:
   `test_edit_outcome_gaps` (1: "a stop that is already in the plan"), `test_eval_proposal` (3: hk07, tc03 failing cases, tc08), `test_failed_sections` (1), `test_hk_disruptions` (2), `test_proposal_db09` (2). Any other failure is yours.
6. **The format hook.** A user-level hook runs `ruff format` on every `.py` file changed with the Edit or Write tool. The existing backend files are not in that style, so one small Edit rewrites hundreds of unrelated lines. Therefore:
   - New files, and files that are already formatted (`app/application/prompt_context.py`, `app/application/plan_focus.py`, everything under `tests/` created on this branch): use Edit/Write normally.
   - Every other existing `.py` under `backend/app/`: change it with a small Python script run through Bash (read text, `assert text.count(old) == 1`, replace, write with `newline=""`) or with `sed`. After each such change run `git diff --stat` and check the file shows only your lines.
   - Put helper scripts in the session scratchpad directory, never in the repo.
7. **Commits:** one plain sentence saying what the software now does (look at `git log --oneline -8` for the style). No "Co-Authored-By", no tool attribution, no prefixes like `feat:`. Commit after each task.
8. **Secrets:** never read, print or commit `.env` or any key. Refer to keys by variable name only.
9. **No URL, price or rating may come from an LLM.** They come from a provider result or are built by code from known fields. This applies to every step.
10. **Retries:** if an approach fails twice, stop, re-read the "read first" lines, and change approach. If two fixes each break something else, stop and report (section 10).
11. **Do not weaken `tests/test_proposal_efficiency.py::test_tc11_local_edit_costs_less_and_keeps_accepted_items`.** It checks that an edit costs fewer tokens than a full replan and currently passes narrowly (about 1096 vs 1158 tokens on case ed01). If your change makes it fail, first try to send less text in the edit prompt; if that is not enough, stop and report.

## 1. Commands

Run backend commands from `backend/`.

| Purpose | Command |
|---|---|
| One test file | `PYTHONPATH=. python -m pytest tests/<file>.py -q -o addopts="" -p no:cacheprovider` |
| Full backend suite (about 8 minutes) | `PYTHONPATH=. python -m pytest -q -o addopts="" -p no:cacheprovider > <scratchpad>/suite.txt 2>&1; grep -E "^FAILED" <scratchpad>/suite.txt; tail -1 <scratchpad>/suite.txt` |
| Lint | `ruff check` (baseline: 532 findings; your new files must have none) |
| Regenerate the plan contract (from repo root) | `PYTHONPATH=backend python -m app.export_contracts` then run `tests/test_export_contracts.py` |
| Frontend typecheck / build (from `frontend/`) | `npm run typecheck` / `npm run build` |
| Frontend unit tests (from `frontend/`, Node 22) | `node --test src/lib/*.test.mjs src/design-system/maps/*.test.mjs` |
| Run the app | start `backend` and `frontend` from `.claude/launch.json` with the preview tool; never start servers with Bash. `LOCAL_ADMIN` mode gives a no-login session. |

Scripts that start the app outside pytest need `ENVIRONMENT=test` in the environment.

## 2. Where things are (read these once)

Backend (`backend/app/`):

| What | Where |
|---|---|
| All models | `domain/models.py` (`TripContext`, `TripPlan`, `DayPlan`, `PlanItem`, `HotelStay`, `TicketOption`) |
| One chat turn, intent overrides | `application/orchestrator.py` (`TurnService._run_turn`; "another place means a new plan" near the calls to `replans_new_destination`) |
| Reading the message into the trip form | `application/understanding.py` (`understand`, `_apply_rules`), `prompts/understand.md`, `prompts/intake.md` |
| Planning a trip | `application/planning.py` (`PlanPath.run`, `PlanPath.run_agents`) |
| Research agents | `application/agents.py` (`AttractionAgent`, `HotelAgent`, `WeatherAgent`, `TicketAgent`; `AgentContext` carries `destination` and `date_range`) |
| Composing days | `application/planner.py` (`Planner.compose(session_id, context, destination, date_range, results, memory_summaries, ...)`, `_build_hotel`, `keep_stay`) |
| Checks after composing | `application/refine.py` (`refine_and_refresh`), `application/validation.py` (`validate_plan`, `repair_plan`, `diff_plans`), `application/assessment.py` |
| Edits | `application/modification.py` (`ModifyPath.run`, `_extract`), `prompts/modify.md` |
| Questions | `application/asking.py` |
| Choices made in the UI | `application/choices.py` (`apply_choice`), `interface/api.py` (`ChoiceRequest`, `choose_option`) |
| Plan text for prompts | `application/prompt_context.py` (`plan_detail`, `plan_context`, `plan_outline`) |
| Providers | `infrastructure/providers/` (`flights.py`: `SerpApiFlightSource.search`, one-way when `return_date` is None; `routes.py`: `GoogleRoutesSource.leg`, `estimate_leg`; `places.py`; `hotels.py`) |
| Offline test sources | `evaluation.py` (`SandboxRegistry`, `SandboxPlaces`, `SandboxHotels`, `SandboxFlights`, `SandboxRoutes`, `SandboxGeocoder`) |
| Limits | `config.py` (`max_days`, `max_tool_calls_per_turn`, `turn_deadline_seconds`, `preplan_budget_seconds`) |

Test helpers you should reuse:

- `tests/plan_samples.py`: `make_plan(...)`, `make_two_city_plan()` — hand-made plans, no sources.
- `tests/test_conversation_context.py`: `RecordingLLM(replies={"<purpose>": "<json>"})` records every prompt by purpose and returns canned replies; `_gateway(settings, llm)`.
- `tests/test_modify_dates.py`: `run_trip(settings, scenario)` runs real turns on a stored trip offline (`trip.turn(message, scope_item_id=...)`, `reply_of(events)`, `plan_event(events)`); `_edit(settings, message, writer=..., scope_item_id=...)` runs one edit.
- `app/eval_efficiency.py`: `build_rig(settings, "Kyoto", ledger)`, `accepted_plan(settings, context)`, `case_context(EvalCase(...), session_id)`.

Frontend (`frontend/src/`): types in `design-system/types.ts`; the page in `App.tsx`; state in `lib/usePathfinder.ts`; API client in `lib/api.ts`; components in `design-system/molecules.tsx` and `design-system/organisms.tsx` (`TripWorkspace`, `HotelOptions`, `TicketCard`, `MapPanel`); map in `design-system/maps/TripMap.tsx`; exports in `lib/export.ts`, `lib/slides.ts`. Plain CSS with `pf-*` classes in `design-system/design-system.css`. No test runner for components: pure helpers get `node:test` files, components get Storybook stories.

## 3. State of the branch when you start

- Step 1 (full-plan chat context) is done and committed.
- Step 2: the plan is written. Two test files exist: `backend/tests/plan_samples.py` now has `make_two_city_plan()`, and `backend/tests/test_plan_segments.py` holds Task 1's tests. **They fail on purpose (10 failures) until you do step 2 Task 1.** Start there, at Task 1 Step 3 (implement).
- In step 2 Task 1, change `backend/app/domain/models.py` by script (rule 6); it is an existing unformatted file.

## 4. Decisions already made (do not ask again)

- Up to **8 cities, 30 days**. More is a question to the traveller, not a silent cut.
- Cities are entered by chat ("Tokyo 4 nights then Kyoto 3") and by an ordered city list in the trip form.
- Order is the traveller's order. Reorder only when they ask ("best order"): nearest neighbour from the origin.
- Nights not given: split evenly, remainder to earlier cities, minimum one each. Stated nights adding up to less than the date range: extra nights go to the first city and the reply says so. More than the range, or both given in one message and in conflict: ask. Nights with no dates: the trip length is their sum.
- **Trip dates stay fixed on edits** unless the traveller says to change them. Added city: takes nights from the longest stay (never below one night). Removed city: its nights go to the city before it (the city after, if it was first). Changed nights: taken from or given to the longest other stay. The reply says which nights moved.
- Between cities: one-way flight search when both ends have an airport and are more than 150 km apart; train/bus and drive estimates when under 900 km. Rank by door-to-door time (flights add 150 minutes), then price. The top option is selected.
- Estimated legs: `estimated=True`, no price unless the routes source gives a fare, assumed to leave at 09:00 local, shown as "about 09:00 – about 11:40 (estimate)". No transit data or no Google key: one option with mode `ground` from `estimate_leg`, labelled "rough estimate", listed under the assessment's `unverified`.
- Budget: one total. Selected transport is costed first; the rest is shared evenly per night across cities.
- Going from one city to several (or back) discards the traveller's flight choice; the reply and the diff say so.
- A city may appear twice (Tokyo → Kyoto → Tokyo). Nothing is keyed by city name. A chat edit naming such a city asks which visit, unless a selected stop or named day settles it.
- The trip returns to the origin unless the traveller says "one way" or "ending in X".
- Paid searches are not capped; hotel and flight results are kept for the session keyed by place and dates.
- Pictures: Google Places photos through a backend route when the Google key the places source already uses is configured, Wikimedia as fallback, hotel thumbnails from the hotel search result.
- The router prompt keeps the short plan outline. Edit prompts carry the plan without per-stop notes.

---

## 5. Step 3 — Plan and research each city

**Idea:** a multi-city trip is planned as several single-city plans, one per city, using the existing pipeline unchanged for each, and then stitched into one plan. The single-city path is not touched.

**Rule of the step:** `PlanPath.run` begins with `if len(context.route) <= 1:` and in that case runs exactly the code it runs today.

### Task 3.1 Limits

- `config.py`: `max_days: int = 30`, new `max_cities: int = 8`, new `segment_deadline_seconds: float = 90.0`, new `segment_tool_calls: int = 100`.
- New `application/limits.py`: `turn_deadline_for(settings, segments: int) -> float` = `turn_deadline_seconds + segment_deadline_seconds * max(0, segments - 1)`, capped at 900; `tool_cap_for(settings, segments: int) -> int` = `max_tool_calls_per_turn + segment_tool_calls * max(0, segments - 1)`.
- Find every use of `turn_deadline_seconds` and `max_tool_calls_per_turn` (`grep -rn`) and pass the segment count of the trip being planned (`len(context.route) or 1`).
- Tests: the two functions for 1, 3 and 8 segments; a 30-day single-city request is accepted and a 31-day one is refused with the existing message.
- Expected test changes: tests that assert the 14-day limit by number. Change the number only.

### Task 3.2 Understanding several cities

Read first: `prompts/understand.md`, `understanding.py:119-182`, `prompts/intake.md`.

- `understand.md`: add to the JSON `"stops": [{"city": "Tokyo", "nights": 4}, {"city": "Kyoto", "nights": null}]` and the rule: "When the message names two or more cities to visit in order, list them in `stops` in that order with the nights stated for each (null when not stated) and set `destination` to the first. One city: `stops` is an empty list." Add: "`one_way`: true only when the traveller says the trip does not return to where it starts."
- `understand()`: when the reply has 2 or more valid stops, write `context.stops` and `context.destination = stops[0].city`. One or none: leave `stops` empty. More than `max_cities`: do not store; return a clarify question "I can plan up to 8 cities in one trip. Which ones should I keep?" (follow how the function already returns a question for a missing slot).
- Add `TripContext.one_way: bool = False`.
- Add a rule fallback in `_apply_rules` for the offline path: the pattern `<City> <N> nights? (then|,|and) <City> <N> nights?` (two or more) fills `stops`. Keep it narrow; a message it does not match changes nothing.
- `intake.md`: replace the rule that returns `destination: null` for several destinations with: return them as `stops` in the order the material gives.
- Tests (with `RecordingLLM(replies={"understand": ...})`): two cities with nights are stored in order; one city leaves `stops == []`; nine cities ask the question and store nothing; "Tokyo 4 nights then Kyoto 3 nights" fills `stops` with no LLM (offline gateway); a single-city message from the existing tests still gives the same context.

### Task 3.3 Route step

New file `application/route_plan.py` (pure functions, no I/O):

```python
class RouteQuestion(Exception):
    """The nights and the dates cannot both be met; `.question` is what to ask."""

def allocate_nights(stops: list[TripStop], total_nights: int) -> tuple[list[int], str]:
    """Nights per stop, and a note for the reply ("" when nothing was adjusted)."""

def build_segments(places: list[Destination], nights: list[int], start: date) -> list[TripSegment]:
    """Consecutive segments: each starts the day the one before ends. ids seg_1, seg_2, ..."""

def nearest_order(origin: GeoPoint | None, places: list[Destination]) -> list[int]:
    """Indexes of `places` in nearest-neighbour order from `origin` (from places[0] when origin is None)."""
```

`allocate_nights` rules, in order: every stop needs at least 1 night, so `total_nights < len(stops)` raises `RouteQuestion("The trip has fewer nights than cities. Which cities should I drop, or should the trip be longer?")`. Stated nights are kept. If stated nights sum to more than `total_nights`, raise `RouteQuestion("The nights you gave add up to N but the dates cover M. Which should I follow?")`. Unstated stops share what is left evenly, remainder to the earlier ones, minimum 1; if that minimum cannot be met, raise the same question. If every stop is stated and the sum is less than `total_nights`, the extra goes to the first stop and the note is `"The dates cover N more night(s) than you listed; I added them to <city>."`.

`total_nights` is `(end - start).days`. When the context has no dates but every stop has nights, the caller sets the end date from the sum.

Tests: `[4, None]` over 7 → `[4, 3]`; `[None, None, None]` over 7 → `[3, 2, 2]`; `[2, 2]` over 6 → `[4, 2]` with the note; `[5, 5]` over 7 raises; 3 stops over 2 raises; `build_segments` for nights `[2, 2]` from 3 December gives 3→5 and 5→7 with ids `seg_1`, `seg_2`; `nearest_order` on three hand-placed points.

### Task 3.4 Transport legs

Read first: `agents.py:801-880` (`TicketAgent`), where `TicketOption(` is built in `planner.py`, `providers/flights.py:60-110`, `providers/routes.py:96-110` and `256-270`.

New file `application/transport_legs.py`:

```python
FLIGHT_MIN_KM = 150
GROUND_MAX_KM = 900
AIRPORT_MINUTES = 150
ESTIMATE_DEPARTS = "09:00"

async def leg_options(
    leg: TransportLeg, origin: Destination, destination: Destination, *,
    registry: Any, toolbox: Toolbox, party_size: int, currency: str,
) -> list[TicketOption]:
    """Ranked options for one leg, the first one `selected`. Every option has `leg_id=leg.id`."""

def rank_options(options: list[TicketOption]) -> list[TicketOption]:
    """By door-to-door minutes (flights + AIRPORT_MINUTES), then price (unpriced last)."""

def build_legs(segments: list[TripSegment], origin: str, one_way: bool) -> list[TransportLeg]:
    """leg_out (origin -> first city, on the first day) when origin is set; leg_<n> between cities on the shared day;
    leg_home (last city -> origin, on the last day) when origin is set and not one_way."""
```

- Flights: call the flight source through `toolbox.call("flights.search", ...)` exactly as `TicketAgent` does, with `return_date=None`, only when both places resolve to an airport (the source already does that lookup; no result means no flight options) and the straight-line distance is over `FLIGHT_MIN_KM`. Convert results with the same function `TicketAgent`/the planner uses today so prices and links are built the same way.
- Ground (distance under `GROUND_MAX_KM`, both places have coordinates): one transit option and one drive option from the routes source's `leg(origin_geo, destination_geo, mode=...)`. Use the modes that source accepts (read it; do not invent a mode name). `mode` on the option is `train` when the longest step is rail, else `bus`, for transit; `drive` for driving. When the routes source is missing, fails, or returns nothing for transit: one option from `estimate_leg` with `mode="ground"`, `name="Ground transport (rough estimate)"`.
- Every ground option: `estimated=True`, `price=None` unless the source gave a fare, `depart_at` = the leg's date at 09:00, `arrive_at` = depart + duration, `booking_url` = `https://www.google.com/maps/dir/?api=1&origin=<from>&destination=<to>&travelmode=transit|driving` built with `urllib.parse.urlencode` from the two place names.
- No options at all for a leg is allowed: the leg stays with none, and the assessment lists "transport from A to B" under `missing`.
- Add offline support: `SandboxRoutes.leg` if it does not exist, returning a fixed duration from distance, so tests run without a network.
- Tests (sandbox registry): Kyoto→Osaka (about 45 km) gives ground options only, all `estimated`, none priced, first selected, departing 09:00; Tokyo→Sapporo (over 800 km) gives flights and ground, ranked by door-to-door time; Hong Kong→Tokyo (over 900 km) gives flights only; `build_legs` for 3 segments with an origin gives `leg_out, leg_1, leg_2, leg_home`, and without `leg_home` when `one_way`; a booking URL contains no key and no personal data.

### Task 3.5 Plan each city and stitch

Read first: all of `planning.py:74-275`, `planner.py:182-200` and `470-490`, `choices.py:328-356` (`_recost`).

1. In `planning.py`, move the body of `run` after the date check into `async def _plan_one(self, *, context, ..., names: set[AgentName] | None = None) -> TripPlan` without changing a line of its logic. `run` calls it for a one-city trip with `names=None`. Run the full suite now: nothing may change. Commit: `Planning one city is a step of its own`.
2. New file `application/stitch.py`:

```python
def city_context(context: TripContext, segment: TripSegment, *, last: bool, budget: float | None) -> TripContext:
    """The trip form as it would be for this city alone: destination = the city, start = arrival day,
    end = the day before departure (the departure day itself for the last city), days to match, stops = [],
    origin = "" (the legs are planned separately), budget = this city's share."""

def stitch(parts: list[TripPlan], segments: list[TripSegment], legs: list[TransportLeg],
           tickets: list[TicketOption], context: TripContext, *, session_id: str, version: int,
           trip_id: str | None) -> TripPlan:
    """One plan from the per-city plans."""

def split(plan: TripPlan) -> list[TripPlan]:
    """The per-city plans of a stitched plan (the inverse of `stitch`, for edits in step 4)."""
```

`stitch` rules:
- `destination` = first segment's; `dates` = first start .. last end; `days` = every part's days in order, each `segment_id` set; `hotels` = each part's chosen stay with `check_in` = segment arrival and `check_out` = segment departure and `segment_id` set; `hotel_options` = every part's options with `segment_id` set; `segments`, `legs`, `tickets` as given.
- **Ids must be unique across the whole plan.** Item, stay and ticket ids from different parts can collide (the offline planner uses ids like `item_p1`). Prefix every id of part *n* (items, alternatives, stays, options, reservations that refer to them) with `s<n>_` when *n* > 1. Keep a mapping and apply it to every field that refers to an id (`TransferLeg.from_item_id`, reservation rows, `affected_item_ids`).
- A travel day (first day of every segment after the first, and day 1 when `leg_out` exists) starts with a `PlanItem` of type `transit` for the leg's selected option: name `"<from> → <to> (<mode>)"`, `start`/`end` from the option's times (for an estimate, 09:00 and 09:00 + duration; clamp `end` to 23:59), `notes="Estimated time"` when estimated, `booking_url` from the option. Then remove from that day every unlocked, unconfirmed item that starts before the leg's arrival plus 60 minutes; if an item overlaps the transit item after that, remove it too. Record each removal in `data_quality.notes` as `"<item> was left out of <date>: you arrive at <HH:MM>."`.
- The last day, when `leg_home` has a selected flight: remove unlocked items that end later than 120 minutes before departure, with the same kind of note.
- `cost`: recompute with the same function `choices._recost` uses, for the whole plan; `budget` = the trip's. `reservations`, `data_quality.gaps/notes/sources_used`, `disruptions`, `video_notes`: concatenated in segment order. `constraints`: recompute with the function the single path uses (find where `ConstraintCheck` rows are built; call it on the stitched plan).
- `summary`: `"<City 1> → <City 2> → ...: N days."` followed by the note from `allocate_nights` when there is one.

3. In `PlanPath.run`, the multi-city branch:
   1. Refuse more than `max_cities` or `max_days` with a `PlanInvalidError`.
   2. Geocode every city (through `toolbox.call("geocode", ...)`, as the single path does). A city that cannot be located: `PlanInvalidError("I could not locate '<city>'. Try the city and country together.")`.
   3. `allocate_nights` (a `RouteQuestion` becomes a clarify reply: see how the turn handler returns a question today and do the same), `build_segments`, `build_legs`.
   4. `leg_options` for every leg, at most 3 at a time (`asyncio.Semaphore(3)`); emit a `progress` event per leg ("Looking up Kyoto → Osaka").
   5. Budget share: `per_night = (budget - sum of selected priced options) / total_nights` (not below 0); each city's share = `per_night * its nights`. No budget: `None`.
   6. `_plan_one` for every segment with `city_context(...)` and `names` excluding the ticket agent, at most 3 at a time, each with its own emit prefix so progress events name the city. A failure in one city fails the plan with that city named.
   7. `stitch`, then `assess_and_improve` on the whole plan exactly as the single path calls it, then `validate_plan`.
4. `validate_plan` gains three checks that run only when the plan has more than one segment: every night from `dates.start` to the day before `dates.end` has a stay (`stay_on`); every pair of consecutive segments has a leg, and a leg that has options has exactly one selected; no item on a travel day starts before its transit item ends. Error texts name the city and date.
5. Until step 4, `ModifyPath.run` on a plan with more than one segment returns the plan unchanged with `change_summary = "I can't change a multi-city plan from chat yet. Use the options on each stay and each leg, or generate the itinerary again."`. Step 4 removes this guard. Questions (the ask path) must work: check `asking.py` uses `stay_on`/`first_stay` and does not crash on a two-city plan (test it with `make_two_city_plan()`).

Tests for 3.5: `city_context` for the middle city of three; `stitch` on two hand-made parts with colliding ids gives unique ids and a valid `TripPlan`; the transit item is first on the travel day and an early stop is removed with the note; `split(stitch(parts))` returns parts with the same item names per day; a full offline turn (`run_trip`-style rig with the sandbox registry) for "Kyoto 2 nights then Osaka 2 nights" stores one plan with 2 segments, 2 stays, legs, 5 days, and every day's stops within 80 km of its own city's centre; a full offline turn for a single city gives a plan equal (ignoring ids generated at random, clocks and the new default fields) to the plan the same turn gave before this step; nine cities are refused; a turn with `[5, 5]` nights over 7 asks the question; an edit message on the two-city plan returns the guard text and the plan is unchanged.

### Task 3.6 Keep paid search results for the session

Read first: how `HotelAgent` and `TicketAgent` call `toolbox.call("hotels.search", ...)` and `("flights.search", ...)`, and what the store offers for per-session data (`grep -n "def " app/infrastructure/store.py | head -80`).

- New `application/search_cache.py`: `class SearchCache` with `get(kind: str, key: tuple) -> list | None` and `put(kind, key, rows)`, stored per session (a table or JSON blob in the existing store; follow how other per-session JSON is stored). Keys: hotels `(city name lower-cased, check_in, check_out, party_size, currency)`; flights `(from, to, date, party_size, currency)`. Entries older than 6 hours are ignored.
- The multi-city branch and step 4's edits read the cache before a hotel or flight search and write after one. The single-city path is not changed.
- Within one turn, a city that appears twice in the route is geocoded and place-searched once; hotels are searched per visit (the dates differ).
- Tests: the second plan of the same route in one session makes no `hotels.search` or `flights.search` tool call (count the tool events); a changed date misses the cache; an entry 7 hours old is ignored (inject the clock).

**Pitfalls:** `TripPlan` validates that `days` cover `dates` exactly: build the stitched days from the segments, do not trust the parts' counts blindly; assert before constructing. The Hong Kong disruption code keys off the destination: it must run inside `_plan_one` per city, not on the stitched plan. Do not send the whole stitched plan to `repair_plan`.

## 6. Step 4 — Multi-city edits by chat

**Idea:** an edit either changes one city (then run the existing single-city edit on that city's part and stitch again) or changes the route (then recompute segments, plan only the new or changed cities, and stitch again).

Read first: `modification.py:205-330` and `1000-1040`, `orchestrator.py` around `replans_new_destination`, `router.py:183-240`, `validation.py` `diff_plans`.

### Task 4.1 Which city an edit is about

New `application/edit_scope.py`:

```python
def segment_for_edit(plan: TripPlan, message: str, scope_item_id: str | None, target_day: int | None
                     ) -> TripSegment | Literal["ask", "all"]:
```

In order: the selected stop's or stay's segment; `target_day` or `focus_day(plan, message, None)` → that day's segment; a city name in the message (case-insensitive, whole word) → its segment, or `"ask"` when that name belongs to two segments; otherwise `"all"`. `"ask"` becomes the question `"You visit <City> twice. Which visit do you mean: <dates A> or <dates B>?"`.

Tests: each branch, including Tokyo → Kyoto → Tokyo.

### Task 4.2 Route changes

- `prompts/modify.md`: allow `day_1..day_30`; add `"route_change": null | {"op": "add" | "remove" | "reorder" | "nights", "city": "...", "after": "city or null", "nights": 2, "order": ["..."]}` with the rule "Only when the traveller asks to add, drop or reorder a city, or to change how many nights a city gets. Never for a stop inside a city." Add `"split_stay": null | {"city": "...", "last_nights": 2}`.
- `ChangeDraft` gains `route_change` and `split_stay` (optional models).
- Rule fallback for the offline path: `add <City>( for N nights)?( after <City>)?`, `(remove|drop|skip) <City>` (only when `<City>` is a segment's name), `<N> nights in <City>`.
- New `application/route_edit.py`:

```python
def apply_route_change(plan: TripPlan, change: RouteChange) -> tuple[list[TripStop], str]:
    """The new ordered stops with nights, and a sentence saying which nights moved."""
```

Rules (section 4): dates fixed; add → nights taken from the longest stay, `RouteQuestion` if that would leave it under 1 or the trip would exceed 8 cities; remove → nights to the city before (after, if first), refuse removing the only city (that is a new plan); nights → taken from or given to the longest other stay; reorder → same nights, new order.

- In `ModifyPath.run` for a route change: build new segments; a segment is **unchanged** when its city and its dates are both the same as before, and its part (`split(plan)`) is reused as is. Changed or new segments are planned with `PlanPath._plan_one`. A reused part whose dates only shifted is re-planned (weather and prices are dated). Legs: keep a leg and its options when both ends and the date are unchanged; otherwise search again. Then `stitch`, version + 1, `context.stops` updated to match and saved.
- One city → several, or back: all legs are rebuilt; add to the reply: `"Your earlier flight choice no longer fits the route, so the flights were looked up again."`
- Split stay: the named city's stay becomes two stays covering its nights; the second is the top-ranked other option for those nights (search hotels for the sub-range through the hotel agent). Stays in a segment must cover its nights with no gap or overlap; add that check to `validate_plan`.
- Orchestrator: when the plan has segments and the form names another place, do not force a new plan if the message is an add (`route_change.op == "add"` or the rule matches); a message that replaces every city still plans anew.

Tests: unit tests for `apply_route_change` (each op, each refusal); offline turns: remove the middle of three cities → remaining cities' days and stays equal to before (compare item names per day and stay names), nights moved to the city before, reply names them; add a city → only that city's agents ran (check the tool events for the other cities are absent); "3 nights in Osaka" moves a night from the longest other stay; going from two cities to one gives a round-trip `leg_out` and the flight sentence.

### Task 4.3 Edits inside one city

- Remove the step 3 guard. For a segment-scoped edit: `parts = split(plan)`; run the existing single-city edit code on that part with `city_context`; `stitch` the parts back; the result's diff is `diff_plans(old_whole, new_whole)`.
- `"all"` scope (for example "slower pace", "cheaper hotels everywhere"): run the edit on every part, at most 3 at a time, then stitch.
- `diff_plans`: stays compared per segment (`stays_in`), entries `{"kind": "hotel", "id": <stay id>, "segment_id": ..., "from": ..., "to": ...}`; transport compared per leg on the selected option, entries `{"kind": "transport", "leg_id": ..., "from": ..., "to": ...}`; new kinds `"city_added"`, `"city_removed"`, `"nights"`. For a single-city plan the existing `hotel` and `flight` entries must stay exactly as they are (same keys, plus `id`).
- The edit prompt for a multi-city plan: `plan_context` already prints one `Stay:` line per stay; add the city to each day header in `plan_detail` when the plan has more than one segment (`Day 3 2026-12-05 [Osaka] "title"`), and to the outline. When an edit is scoped to one city, send the outline of the whole trip plus the detail of that city's days only (`plan_detail(days=[...])`), to keep the prompt small.

Tests: swap a stop in the second city → only that city's day changed, other cities byte-equal (`model_dump` of their days and stays); "make the Osaka hotel cheaper" changes `hotel_b` only and the diff entry carries its id; a pace change with nothing selected touches every city; all the step 1 prompt tests still pass; TC-11 still passes.

## 7. Step 5 — UI

Work in `frontend/src`. Use Edit/Write normally (the hook only touches `.py`). After each task run `npm run typecheck`.

Read first: `design-system/types.ts:34-200` and `349-401`, `organisms.tsx:1180-1420` (`TripWorkspace`), `1076-1180` (`HotelOptions`, `TicketCard`), `1598-1700` (`MapPanel`), `maps/TripMap.tsx:47-75`, `lib/usePathfinder.ts:716-894`, `lib/api.ts:181`.

### Task 5.1 Types and helpers

- `types.ts`: `TripSegment`, `TransportLeg`, `TripStop`; `TripPlan.segments`, `TripPlan.legs`; `DayPlan.segment_id?`, `HotelStay.segment_id?`, `TicketOption.leg_id?`, `estimated?`, mode adds `"drive" | "ground"`; `TripContext.stops?`, `one_way?`. All optional so plans from older versions render.
- New `lib/segments.ts` (pure, with `lib/segments.test.mjs`): `segmentsOf(plan)` (one synthetic segment when the plan has none), `daysBySegment(plan)`, `stayOn(plan, date)`, `staysIn(plan, segmentId)`, `optionsFor(plan, legId)`, `chosenFor(plan, legId)`, `legInto(plan, segmentId)`, `routeLabel(plan)` ("Tokyo → Kyoto"; the single name for one city), `segmentOfDay(plan, date)`. Mirror the backend rules in the step 2 plan exactly, including the shared day belonging to the city arrived in.
- Tests: each helper on a two-city fixture and on a plan with no `segments`.

### Task 5.2 Trip form

- `TripContextForm`: the destination field becomes a list of city rows (city input with the existing place suggestions, optional nights number), with add, remove and move up/down buttons, at most 8 rows. One row looks like today's field. Saving writes `stops` (empty when one row) and `destination` = first city. Add a "one way" checkbox next to the origin.
- Backend: confirm `PATCH /api/journeys/{id}/state` accepts `stops` (it validates with `TripContext`).
- Story: form with three cities.

### Task 5.3 Workspace

- Header: `routeLabel(plan)`; the clock uses the timezone of `segmentOfDay(plan, activeDay)`.
- Body: for each segment a section headed `"<City> · <arrive> – <leave> · N nights"`; before it (when there is one) a `TransportLegCard` for `legInto`; inside it one stay card per stay in `staysIn` with that stay's `HotelOptions` (options filtered by `segment_id`, `chosenId` = that stay's id, `onChoose(optionId)` → `chooseOption({kind: "hotel", target_id: stay.id, option_id})`), then that segment's days. After the last segment, the home leg.
- `TransportLegCard` (new, in `organisms.tsx` next to `TicketCard`): the chosen option (reuse `TicketCard` rendering), a list of the other options with a Choose button for every mode (`chooseOption({kind: "transport", target_id: leg.id, option_id})`), the label "estimate" and "about" before times when `estimated`, no price shown when there is none.
- `DayStrip`: a city label above each group of days.
- `usePathfinder`: `hotelChanged: boolean` becomes `changedStayIds: Set<string>` and add `changedLegIds: Set<string>`, from the diff entries' `id` and `leg_id`; highlight those cards. A single-city plan's diff has `id` too (step 4).
- Selection labels sent with chat stay as they are.
- Stories: three-city plan, a split stay (two stays in one city), a leg with a flight and an estimated train.

### Task 5.4 Map

- `MapPanel`: centre = the active day's segment's `destination.geo`; hotel = `stayOn(plan, activeDay)`; replace the 400 km filter with "within 150 km of the active segment's centre"; on a travel day draw a straight line from the previous city's centre to this one's, labelled with the chosen mode; footer shows the active city.
- Add a "Whole trip" toggle: every city as a numbered marker in order, lines between them, fit to all.
- `TripMap`: remove the hard-coded Hong Kong fallback centre; with no centre, fit to the points given, else show the world.
- Backend map payload (`interface/api.py` near the function that returns `"center": plan.destination.geo`): add `segments` (id, name, centre, dates) and `stays`; keep `center` and `hotel` as they are for old clients.
- Test: `mapGeometry`-style pure helper for the filter and the overview bounds.

### Task 5.5 Exports and versions

- `lib/export.ts` and `lib/slides.ts`: use `routeLabel`; group days under a city heading; list each stay with its dates and each leg's chosen option.
- Version list: the backend summary row (`infrastructure/store.py`, where `"destination"` is written for versions) writes the route label for multi-city plans.

### Task 5.6 Check in the running app

Start `backend` and `frontend` from `.claude/launch.json` with the preview tool. In the app: plan "Kyoto 2 nights then Osaka 2 nights from Hong Kong" and a single-city trip. For each, read the page (`read_page`), check the console for errors, choose another stay in the second city and another option on one leg, switch the map to the whole trip, and take one screenshot of each plan. Check at phone width once. If no LLM or provider keys are configured the app uses its offline sources; say so in the report. Do not enter any credentials.

## 8. Steps 6 and 7

### Step 6 — Links, ranking, pictures

Task 6.1 **Links.** `models.py`: `class Link(BaseModel): kind: Literal["booking", "official", "map", "tickets", "menu"]; url: str; label: str = ""`; `links: list[Link] = []` on `PlanItem`, `PlanAlternative`, `HotelStay`, `TicketOption`. New `application/links.py`: `links_for_place(row) -> list[Link]`, `links_for_stay(stay)`, `links_for_ticket(ticket)`. Sources: the place's website → `official`; its Google Maps URI, else `https://www.google.com/maps/search/?api=1&query=<lat>,<lng>` (coordinates only) → `map`; the hotel search link → `booking`; the flight query URL → `booking`; an estimated leg's directions URL → `map`. Only `http`/`https` URLs are kept. `booking_url` keeps its value. Fill links where the planner builds each object (`grep -n "booking_url" app/application/planner.py`). Tests: each function; a `javascript:` URL is dropped; every link in a planned offline trip starts with `https://`. UI: a `LinkRow` (Book / Website / Map) on stop cards, stay cards and transport cards, opening in a new tab with `rel="noopener noreferrer"`.

Task 6.2 **Ranking.** Fields `rank: int | None`, `score: float | None`, `reasons: list[str] = []` on `PlanAlternative`, `HotelStay`, `TicketOption`. New `application/ranking.py` with fixed weights:
- stays: price fit to the city's per-night budget 0.35, rating 0.25, distance to that city's stops 0.20, stay-research match 0.20;
- meal and stop alternatives: rating 0.40, distance 0.35, price level 0.25;
- transport: door-to-door time 0.60, price 0.40.
Each factor is scaled 0..1 within the option list; a missing value scores 0.5 and is never given as a reason. `reasons`: up to three short phrases from the highest-scoring factors, from a fixed table (for example `"closest to your stops"`, `"best rated"`, `"within budget"`, `"fastest door to door"`, `"matches what you asked of the room"`). They are written by code, never by the LLM, and go through the same translation path as other plan text (`application/translation.py`). The chosen option is not re-ordered by ranking; ranking only labels. Tests: ordering on hand-made lists, ties, missing values, reasons text. UI: rank badge and reason line on options, price, rating and distance beside them.

Task 6.3 **Pictures.** `class ImageRef(BaseModel): url: str; attribution: str = ""; source: str`; `image: ImageRef | None` on `PlanItem`, `PlanAlternative`, `HotelStay`.
- Before coding, check the exact response fields against the provider code and fixtures already in the repo (`providers/places.py`, `providers/hotels.py`, their tests); do not rely on memory for field names.
- Google: request the place's photo reference in the existing places call (add the photo field to the field mask) and store `ImageRef(url="/api/images/place?ref=<reference>", source="google", attribution=<author attribution>)`.
- New route `GET /api/images/place?ref=...`: `ref` must match `^places/[A-Za-z0-9_-]+/photos/[A-Za-z0-9_-]+$` or the request is refused with 422 (this stops the route being used to fetch other URLs). It fetches the photo with the server's key, caches the bytes on disk under the app's data directory keyed by a hash of `ref`, and returns them with a one-week cache header. The key never appears in a plan, a URL sent to the browser or a log line. Rate-limit it with the existing limiter.
- No Google key: look up a Wikimedia thumbnail by the place's Wikivoyage/Wikipedia title where the source row has one; `source="wikimedia"`, attribution from the API. No title: no image.
- Hotels: the thumbnail URL in the hotel search result, `source="serpapi"`.
- Tests: the `ref` check (good, path traversal, full URL); the cache is used on the second call (fake HTTP); a place with no photo has `image is None`; nothing in `plan.model_dump_json()` contains the key's value (use a fake key in settings).
- UI: image on stop, alternative and stay cards with `loading="lazy"`, fixed aspect ratio, attribution in small text; nothing rendered when there is no image.

### Step 7 — Traveller profile

- `models.py`: `class TravellerProfile(BaseModel): pace: Literal["relaxed", "balanced", "packed"] = "balanced"; with_children: bool = False; purpose: Literal["leisure", "business"] = "leisure"; step_free: bool = False`. `TripContext.profile: TravellerProfile`. A stored context with the old `pace` string: a validator maps it (`"slow"`/`"relaxed"` → relaxed, `"fast"`/`"packed"` → packed, anything else → balanced). Keep `pace` readable as a property returning `profile.pace` so existing code works; find writers with `grep -rn "\.pace" app`.
- `understand.md` and `understand()`: return and store the four values when the message states them ("with two kids", "business trip", "wheelchair", "take it slow").
- Effects, all in code (no prompt decides them):
  - pace: stops per day 2 / 4 / 5 (`max_attractions_per_day` becomes the value for balanced; grep its uses), and gaps between stops 45 / 30 / 15 minutes where the scheduler has such a gap.
  - with_children: candidates whose source types include a family-suited type (zoo, aquarium, park, playground, amusement_park, museum with "children"/"science") get a ranking boost of 0.15 before selection; nothing is scheduled to end after 19:00; a single transfer over 45 minutes is flagged in the assessment.
  - business: stays ranked with distance to centre weight 0.35 and price 0.20; at most two stops a day, scheduled after 17:00, unless the message asks otherwise.
  - step_free: request the accessibility field from the places source (check its name in the provider's docs or fixtures first); prefer places marked accessible; places with no data are listed under the assessment's `unverified` as "step-free access not confirmed: <name>"; walking transfers over 800 m are switched to transit or drive where the routes source offers one.
- Trip form: a pace select and three checkboxes.
- Tests: the same offline request with relaxed and packed gives different stop counts per day; with_children ends by 19:00; business has at most two stops, all after 17:00; step_free lists unconfirmed places; an old stored context loads with the right pace.

## 9. Exit checklist for every step

Report each line with the command and its last output line.

1. Full backend suite: the only failures are the 9 in rule 5.
2. `ruff check`: not more than 532 findings; `ruff check <your new files>` clean.
3. `tests/test_export_contracts.py` passes (regenerate the contract when a model changed).
4. `git diff --stat <step start commit>..HEAD -- backend/app`: no pre-existing file shows hundreds of changed lines from formatting.
5. From step 5 on: `npm run typecheck`, `npm run build`, the node tests, and the app check of Task 5.6 repeated for what the step changed.
6. A single-city turn through the offline rig gives the same plan as before the step (the comparison test from Task 3.5).
7. `git log --oneline` shows one commit per task with no attribution lines.

## 10. When to stop and report instead of continuing

- The TC-11 test fails and trimming the edit prompt does not fix it.
- A provider response does not have the field this plan assumes (photos, accessibility, transit fares).
- An existing test outside "Expected test changes" fails and you cannot find the cause in two attempts.
- The spec and this plan conflict in a way that changes what the traveller sees.
- Anything would need a push, a merge, a key, a paid live search, or deleting files you did not create.

Report in this form: what is done (commits), what failed (exact command and output), what you tried, and the decision you need.
