# Choose-and-Lock Stays and Transport: Plan

Written for an agent that has not seen the conversation. Follow it in order. Use the names and numbers given.

**Read first:** section 0 ("Rules for the whole job") and section 1 ("Commands") of `docs/superpowers/plans/2026-11-12-multi-city-handoff.md`. Every rule there applies here (test first, the 9 known failing backend tests, existing backend `.py` files changed by script because of the format hook, plain-sentence commits with no attribution, no push or merge, do not weaken the TC-11 test).

**Branch:** create `choose-and-lock` from `main` (head `fe07a79` or later). Never commit to `main`.

## What the traveller reported

They shared a link to a 14-day Japan itinerary (Tokyo, Kanazawa, Kyoto, Osaka, Hiroshima) and got: one "city" called Japan, one hotel, no transport between places. They want:

1. Every city to have its own hotel choice, and every hop between places its own transport choice (flight, train, bus/shuttle), shown as components that appear for whatever that trip needs.
2. After they lock in a hotel or a transport option, only the chosen one is shown.
3. A button to re-choose, which brings the options back.

## What was found

- That plan was stored before the multi-city code existed (stored 2026-11-13 10:44 +0800; the model work landed from 15:04 +0800). Its destination is the country "Japan", it has no `segments` and no `legs`.
- The trip form for that session still has `destination: "Japan"` and no `stops`. `backend/app/application/intake.py` (about lines 250-266) only takes stops from shared material when the form's destination is empty or equals the first stop, so a form that already says "Japan" keeps ignoring the cities.
- The UI today shows a stay card with its other options folded away, and the leg's other options folded away. There is no "locked" state for a leg and no way to lock or unlock from the page.
- There is no transport mode for a shuttle, and a short hop only gets "train/bus" when Google Routes returns transit.

## Goals

1. A shared itinerary or a message that names several cities always becomes a multi-city trip, even when the form already holds a country or region as its destination.
2. Each stay and each leg is a **choice slot** with two states:
   - **Open:** every option is shown, the suggested one marked; each has a "Lock in" button.
   - **Locked:** only the chosen option is shown, with a "Change" button that reopens the slot.
3. A leg's slot shows a tab per kind of transport that has options (Flight, Train, Bus / shuttle, Drive). Kinds with no options are not shown.
4. Chat edits never replace a locked choice without saying so.
5. Single-city trips get the same slots (one stay, the outbound leg).

## Non-goals

- Booking. Locking in is the traveller's decision inside the plan, not a reservation.
- Locking by chat ("lock the Kyoto hotel"). The buttons do it.
- Airport-to-hotel transfers inside a city. A leg is city to city. (Open question for the user; do not build.)
- Proposing cities for a bare country ("Japan, 14 days" with no cities named). Task 1.3 asks the traveller instead.

## Anti-goals

- A locked slot that silently changes after a chat edit.
- An estimate shown as a quoted fare.
- A new plan version for every lock or unlock click.
- Plans stored before this change failing to load. (`locked` defaults to false, so they open with every slot open.)

---

## Part 1 — Several cities always make a multi-city trip (backend)

### Task 1.1 Stops from shared material win over a country or region

Read first: `backend/app/application/intake.py` lines 95-110 and 240-270; `backend/app/prompts/intake.md`.

Change the rule that stores stops. New rule: when the draft has 2 to 8 valid stops and the form has no stops yet, store them and set `destination` to the first stop **unless** the form's current destination is itself one of the stops' cities at a position other than first (then keep the traveller's order question out of it: store the stops as given and set destination to the first). In short: the only case where stops are not stored is when the form already has stops.

When the stored destination is replaced this way and it differed from the first stop, add to the intake notice (the `filled` list the page shows) the entry `"stops"`, so the page says the cities were read.

Tests (`backend/tests/test_intake_stops.py`, new):
- draft `{destination: "Japan", stops: [Tokyo, Kanazawa, Kyoto, Osaka, Hiroshima]}` on an empty form → 5 stops in order, destination `"Tokyo"`;
- the same draft on a form whose destination is already `"Japan"` → the same result;
- a form that already has stops → unchanged;
- one stop → `stops` stays empty and the existing single-destination behaviour is unchanged (run the existing intake tests).

### Task 1.2 A plan made for a country is planned again by city

Read first: `backend/app/application/orchestrator.py` where `route_differs` / `edit_route_from_form` are called (step 5 added them).

When the form now has 2+ stops and the stored plan has one segment whose city is not one of them (the "Japan" plan), the next planning turn ("generate again", or the same turn as the intake when no plan exists yet) plans the route from scratch with the multi-city path. Confirm this already happens through `route_differs`; if `route_differs` compares only stop lists and misses "plan has no stops, form has stops", fix that comparison.

Test: offline rig (`backend/tests/multi_city_rig.py`): store a single-city plan for "Japan", set the form's stops to Tokyo and Kyoto, send the page's generate-again message → the stored plan has 2 segments, a stay in each, and legs.

### Task 1.3 A bare country or region asks for cities

Read first: the geocoder result type in `backend/app/infrastructure/providers/` (what a geocode result says about the kind of place: country, region, city). **If no provider result in the repo distinguishes a country from a city, stop and report; do not guess from the name.**

When planning a one-stop trip whose geocoded place is a country or a first-level region, and the trip is 4 days or longer, reply with the question `"<Place> is a whole country/region. Which cities do you want to visit, and in what order? For example: Tokyo 4 nights, then Kyoto 3."` and do not plan. Shorter trips plan as today.

Tests: "Japan, 14 days" asks; "Japan, 2 days" plans as before; "Kyoto, 14 days" plans as before.

## Part 2 — Lock state and API (backend)

### Task 2.1 Model

Read first: `HotelStay.locked` and `TransportLeg` in `backend/app/domain/models.py`; how `locked` on a stay is already respected in `choices._choose_hotel` and in `modification.py` (`grep -n "\.locked" backend/app/application/*.py`).

- `TransportLeg.locked: bool = False`.
- `TripPlan.open_choices() -> list[tuple[str, str]]`: `("hotel", stay.id)` for every chosen stay that is not locked and has at least 2 options in its segment, and `("transport", leg.id)` for every leg that is not locked and has at least 2 options. A slot with one option or none needs no decision and is never "open".
- Regenerate the contract (`PYTHONPATH=backend python -m app.export_contracts`).

Tests: defaults; `open_choices` on the two-city sample plan before and after locking; a stored plan without the field loads.

### Task 2.2 Lock and unlock without a new version

Read first: `backend/app/interface/api.py` `choose_option` (near the `ChoiceRequest` model) and the rooms route just below it, which already saves a change onto the current plan version in place — use the same store call for the lock.

- `ChoiceRequest` gains `lock: bool = False`. When true, after the choice is applied the chosen stay's `locked` (kind `hotel`) or the leg's `locked` (kind `flight`/`transport`) is set to true in the same new version.
- Choosing the option that is already chosen with `lock: true` only locks: no new version; the latest version is updated in place.
- New route `POST /api/journeys/{session_id}/lock` with body `{"kind": "hotel" | "transport", "target_id": "<stay id or leg id>", "locked": true | false}`. It updates the latest plan version in place and returns the plan. Unknown target → 404. Same session check and rate limit as `choose_option`. Only the latest version can be changed (same rule as choosing).
- `_choose_hotel`: today a locked stay refuses a new choice. Keep that: the page unlocks first ("Change"), then chooses. `_choose_flight`: refuse likewise on a locked leg with `ChoiceRefused("That leg is locked in; press Change before choosing another.")`.
- Add the new route to whatever list `backend/tests/test_frontend_routes.py` checks once the frontend calls it.

Tests (`backend/tests/test_choice_lock.py`, new): choose with `lock: true` → version + 1 and the stay is locked; choose the current option with `lock: true` → same version number, locked; unlock → same version number, unlocked; choosing on a locked leg is refused with 409 (or the status `ChoiceRefused` maps to today); unlock then choose works; lock on an older version is refused; a locked stay or leg survives `model_dump`/`model_validate`.

### Task 2.3 Chat edits respect locks

Read first: `backend/app/application/multi_edit.py` (route changes, per-city edits), `backend/app/application/modification.py` where a locked stay is kept.

- A per-city edit never changes a locked stay (already true for stays; add a test for the second city of a multi-city plan).
- A route change keeps a locked leg and its chosen option when both ends and the date are unchanged. When the route change removes or alters a locked leg, or changes a locked stay's dates, the lock is released and the reply ends with one sentence per released lock: `"Your locked choice for <A → B | the stay in City> no longer fits the new route, so it is open again."`
- "Make the hotels cheaper everywhere" skips locked stays and says `"<Hotel> in <City> is locked in and was left as it is."`

Tests: one per bullet, through the offline multi-city rig.

## Part 3 — Bus / shuttle options (backend)

Read first: `backend/app/application/transport_legs.py` (constants, `leg_options`, `rank_options`).

- `TicketOption.mode` gains `"shuttle"`. (Keep every existing value.)
- For a leg under 300 km where the transit lookup returned no bus option, add one estimated option: `mode="bus"`, `name="Bus or shuttle (estimate)"`, duration = the drive duration × 1.3 (from the drive option already computed; if there is no drive option, from `estimate_leg` in drive mode × 1.3), `estimated=True`, no price, leaving at 09:00, `booking_url` = the Google Maps directions URL with `travelmode=transit`. One such option per leg at most.
- `"shuttle"` is only used when a source says so (a transit vehicle type the routes source labels as a shuttle). Do not label an estimate "shuttle".
- Ranking is unchanged (door-to-door time, then price).

Tests: Kyoto → Osaka (45 km, sandbox routes with no transit) has options of modes `ground` or `train`, `bus` and `drive`, all `estimated`; a 500 km leg gets no estimated bus; a leg where transit already returned a bus gets no second bus.

## Part 4 — Choice slots (frontend)

Work in `frontend/src`. Use Edit/Write normally. Run `npm run typecheck` after each task.

Read first: `design-system/organisms.tsx` — `HotelOptions` (about line 1097), `TicketCard`, `TransportLegCard` (about 1269), `StayCard`, and `TripWorkspace` from about line 1372 to 1620 (both the multi-city branch and the single-city branch); `lib/api.ts` `chooseOption`; `lib/usePathfinder.ts` where a choice is sent and the plan replaced; `lib/segments.ts`.

### Task 4.1 Types, API client, helpers

- `types.ts`: `TransportLeg.locked?: boolean`; `TicketOption.mode` adds `"shuttle"`.
- `lib/api.ts`: `chooseOption` takes `lock?: boolean`; new `setChoiceLock(sessionId, {kind, target_id, locked})` calling `POST /api/journeys/{id}/lock`.
- `lib/usePathfinder.ts`: `lockChoice(kind, targetId, optionId)` (choose with `lock: true`) and `reopenChoice(kind, targetId)` (unlock); both replace the plan from the response. While a request is running the slot's buttons are disabled.
- New `lib/choices.ts` (pure, with `lib/choices.test.mjs`):
  - `slotState(locked: boolean, optionCount: number): "locked" | "open" | "single" | "empty"` — `empty` for 0 options, `single` for exactly 1 unlocked option, `open` for 2+ unlocked, `locked` when locked.
  - `transportKinds(options): {kind: "flight" | "train" | "bus" | "drive", options: TicketOption[]}[]` — groups by kind in that order; `bus` holds modes `bus`, `shuttle` and `ground`; `train` holds `train`; `flight` holds `flight`; `drive` holds `drive`; legacy modes `ticket` and `ferry` go under `bus`. Empty groups are left out.
  - `openCount(plan): number` and `slotCount(plan): number` for the summary line (mirror `TripPlan.open_choices`: slots with 2+ options).
- Tests: each function, including a plan with no `legs`.

### Task 4.2 `ChoiceSlot`

New component in `organisms.tsx` (next to `TransportLegCard`), generic over the option type:

```ts
function ChoiceSlot<T extends { id: string }>(props: {
  title: string;                 // "Stay in Kyoto · 3 nights", "Kyoto → Osaka · 5 Dec"
  icon: IconName;
  state: "locked" | "open" | "single" | "empty";
  chosen: T | null;
  options: T[];                  // every option, the chosen one included
  renderOption: (option: T, role: "chosen" | "option") => ReactNode;
  tabs?: { label: string; options: T[] }[];   // when given and more than one, options are shown per tab
  emptyText: string;
  busy?: boolean;
  changed?: boolean;             // highlight after a plan update
  onLock?: (optionId: string) => void;
  onReopen?: () => void;
})
```

Behaviour:
- `locked`: the title row with a "Locked in" badge, the chosen option only, and a **Change** button (`onReopen`). No other option is in the DOM.
- `open`: the title row with a "Choose one" badge; every option rendered (per tab when `tabs` has more than one group; the tab holding the suggested option is selected first); the suggested option (the currently chosen one) carries a "Suggested" badge; every option has a **Lock in** button (`onLock(option.id)`).
- `single`: the one option with a **Lock in** button and the note "Only one option was found."
- `empty`: `emptyText`.
- Without `onLock`/`onReopen` (an older version is being viewed) no buttons are shown and the slot shows the chosen option plus a count of the others.
- Keyboard: buttons are real `<button>`s; tabs use the existing tab pattern in the design system if there is one (`grep -n "role=\"tab\"" frontend/src/design-system`), else a row of toggle buttons with `aria-pressed`.
- Text goes through `useI18n`; add keys to `lib/locales/en.ts`, `zh-Hant.ts`, `zh-Hans.ts`: `choice.locked` ("Locked in" / 已鎖定 / 已锁定), `choice.chooseOne` ("Choose one" / 請選擇一項 / 请选择一项), `choice.suggested` ("Suggested" / 建議 / 建议), `choice.lockIn` ("Lock in" / 鎖定 / 锁定), `choice.change` ("Change" / 重新選擇 / 重新选择), `choice.onlyOne`, `choice.others` ("{count} other options"), `choice.kind.flight`, `choice.kind.train`, `choice.kind.bus` ("Bus / shuttle" / 巴士／接駁車 / 巴士／接驳车), `choice.kind.drive`, `choice.summary` ("{open} of {total} choices still open" / 尚有 {open}/{total} 項待選 / 还有 {open}/{total} 项待选), `choice.allLocked`.
- CSS in `design-system/design-system.css` with `pf-choice*` classes, using existing tokens. Works at 375 px width with no horizontal scroll.

### Task 4.3 Use the slots

In `TripWorkspace`:
- **Stay slot** (replaces `StayCard` + the folded `HotelOptions` in both the multi-city and the single-city branch): `title` = "Stay in <City> · N nights" (single-city: "Your stay · N nights"); `options` = that stay's options (`optionsOfStay`), `chosen` = the stay; `state` = `slotState(stay.locked, options.length)`; `renderOption` reuses the existing hotel option card (price for these dates, rating, distance, rank and reasons, picture, links, the rooms loader); `onLock` → `lockChoice("hotel", stay.id, optionId)`; `onReopen` → `reopenChoice("hotel", stay.id)`. Selecting the stay for chat (the existing `onSelectItem(hotelScopeItem(stay))`) stays on the chosen card.
- **Leg slot** (replaces `TransportLegCard`): `title` = "<From> → <To> · <date>"; `tabs` = `transportKinds(options)` with the `choice.kind.*` labels; `renderOption` reuses `TicketCard` (estimate label, "about" times, no price when none, links); `onLock` → `lockChoice("transport", leg.id, optionId)`; `onReopen` → `reopenChoice("transport", leg.id)`; `emptyText` = "No transport options were found for this leg." with the directions link when both places are known.
- The single-city branch gets a leg slot for its outbound leg when the plan has tickets (replacing the current flight cards), using the same component.
- Above the first city: a summary line from `openCount`/`slotCount` — "2 of 5 choices still open", or "All choices locked in". Clicking it scrolls to the first open slot.
- A slot whose id is in `changedStayIds` / `changedLegIds` passes `changed`.
- Remove `TransportLegCard` and the folded hotel `Collapsible` if nothing else uses them; keep `HotelOptions`' card rendering as the function `renderOption` calls.

Stories (`design-system/stories/`): a three-city plan with every slot open; the same with every slot locked; a leg with flight, train and bus tabs; a leg with no options; a single-city plan.

### Task 4.4 Check in the running app

Use the offline launcher approach from step 5 (sandbox sources, no keys) or `.claude/launch.json` `backend` + `frontend`. In the app:
1. Paste a message naming three cities with nights; confirm a slot per stay and per leg, all open.
2. Lock one hotel and one leg; confirm each shows only the chosen option and a Change button; reload the page; confirm they are still locked and the version number did not change for the lock alone.
3. Press Change on one; confirm the options are back.
4. Ask in chat to remove the middle city; confirm the reply names the released lock.
5. Check 375 px width and dark mode once; check the console for errors.
Take one screenshot of an open leg slot and one of a locked stay slot. Do not enter credentials.

## Exit checklist

Use section 9 of the handoff plan (backend suite = only the 9 known failures; ruff not above the current count; contract regenerated; typecheck, build, node tests; app check). Add: `git grep -n "TransportLegCard"` returns nothing outside stories that were updated.

## Stop and report when

- No provider result tells a country from a city (Task 1.3).
- The lock cannot be saved in place without a new version (Task 2.2) — report how the rooms route saves and what blocks reusing it.
- Any case in section 10 of the handoff plan.
