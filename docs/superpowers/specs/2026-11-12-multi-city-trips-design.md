# Multi-city trips, richer recommendations and full-plan chat context

Date: 2026-11-12. Branch: `multi-city-trips`.

## Intent

PathFinder plans one city, one hotel and one round-trip flight. A real trip
is often several cities, a stay in each, and a mix of flights and trains
between them, adjusted over many chat turns. This work makes the planner and
the UI handle that end to end, shows what a traveller needs to act on a
recommendation (why it was picked, price, rating, where it is, a picture, a
link), and gives the model the whole plan when the traveller's message is
not about one particular day or stop.

## Goals

1. One itinerary can cover up to **8 cities and 30 days**, across countries.
2. One or more stays per city; every night of the trip is covered by exactly
   one stay; each stay is chosen from its own ranked options.
3. A transport leg between consecutive cities (and from/to the origin), each
   with its own ranked options and its own choice.
4. Recommendations carry rank, a short reason, price, rating, location and
   links (booking, official site, map), plus a picture where one exists.
5. Planning adapts to pace, family travel, business travel and accessibility.
6. Chat edits work on a multi-city plan: add, remove or reorder a city,
   change nights or dates, change one stay, pick a transport option. Only
   the affected cities are researched again.
7. When no stop is selected and the message names no day, the model gets the
   full plan in detail.
8. A single-city trip behaves as it does today.

## Non-goals

- Booking or payment. Links go to the provider.
- A fare provider for trains, buses or ferries (see "Between cities").
- Travellers starting from different origins; cruises; visas.

## Anti-goals

- A URL, price or rating written by the LLM. Every one comes from a research
  result or is built by code from known fields.
- An estimated ground leg shown as if it were a quoted fare.
- Plans saved before this change failing to load or render.
- A prompt that grows with the whole trip where a per-city prompt would do.

## Decided with the user

- **Entering cities:** chat ("Tokyo 4 nights then Kyoto 3") and the trip
  form, which gets an ordered city list with optional nights per city.
- **Between cities:** searched flights per leg; train, bus and drive as
  estimated options (duration from Google Routes, a search/booking link),
  ranked against flying.
- **Pictures:** Google Places photos when the Google key is set, Wikimedia /
  Wikivoyage as fallback, hotel thumbnails from the hotel search results.
- **Size:** 30 days, 8 cities.
- **Full-plan context:** as goal 7.
- **Trip dates on edits:** the trip keeps its start and end dates unless the
  traveller says to change them. An added city takes its nights from the
  longest stay (never below one night); a removed city's nights go to the
  city before it (the city after, if it was first); changing one city's
  nights takes from or gives to the longest other stay. The reply says which
  nights moved.
- **Paid searches:** no cap. Every city and leg is searched; hotel and
  flight results are kept for the session keyed by place and dates, so an
  edit pays only for the cities and legs that changed.

Everything below is the builder's proposal (my call) unless it restates one
of the above.

## Model (`backend/app/domain/models.py`)

`TripContext`
- New `stops: list[TripStop]`, `TripStop = {city: str, nights: int | None}`,
  in travel order, max 8.
- `destination: str` stays. With one stop it is that city; with several it
  is the first city. Code that needs the route reads `stops`. An empty
  `stops` with a `destination` means one stop (old saved contexts).
- New `profile: TravellerProfile = {pace: relaxed|balanced|packed,
  with_children: bool, purpose: leisure|business, step_free: bool}`. The
  existing `pace` field moves into it (old value read on load).
- `days` max 60 stays; `max_days` config goes 14 → 30.

`TripPlan`
- New `segments: list[TripSegment]`, always at least one.
  `TripSegment = {id, destination: Destination, dates: DateRange}` where
  `dates.start` is the arrival day and `dates.end` the departure day.
  Consecutive segments share that day (leave A and arrive B on one date).
- `destination` stays and equals `segments[0].destination`.
- A validator builds one segment from `destination` + `dates` when a stored
  plan has none, so old rows load unchanged.
- `DayPlan.segment_id`: the city the traveller sleeps in that night; the
  last day belongs to the last segment. A travel day starts with the
  transport leg as an item, then that city's stops.
- `HotelStay.segment_id`. `hotels` holds the chosen stays in date order.
  Stays inside a segment must cover its nights with no gap or overlap; a
  segment normally has one, and a chat edit can split it ("a different hotel
  for the last two nights in Tokyo"). `hotel_options` entries carry
  `segment_id` and the `check_in/check_out` they were searched for.
- New helpers `stay_on(date)`, `stays_in(segment_id)`, `segment_of(date)`.
  Every `hotels[0]` read (46 sites) moves to one of these. No code outside
  the model indexes `hotels` by position.
- `TransportLeg = {id, from_place, to_place, date, kind:
  outbound|between|return}` in new `legs: list[TransportLeg]`.
  `TicketOption` gains `leg_id`, `mode` (flight|train|bus|drive|ground) and
  `estimated: bool`. Exactly one option is selected **per leg**.
  A single-city trip keeps today's shape: one `outbound` leg whose flight
  options carry the return (`return_*` fields), no `return` leg. A
  multi-city trip uses one-way options on every leg, including home.
- `Link = {kind: booking|official|map|tickets|menu, url, label}`; `links:
  list[Link]` on `PlanItem`, `PlanAlternative`, `HotelStay`, `TicketOption`.
  `booking_url` stays and mirrors the booking link.
- `Recommendation` fields on `PlanAlternative`, `HotelStay` (as option) and
  `TicketOption`: `rank: int`, `score: float`, `reasons: list[str]` (max 3).
- `ImageRef = {url, attribution, source}`; optional `image` on `PlanItem`,
  `PlanAlternative`, `HotelStay`.
- `contracts/trip_plan.schema.json` is regenerated.

## Pipeline

**Understanding.** `understand.md` returns ordered stops with nights and the
profile. `intake.md` stops returning `destination: null` for material that
covers several places and returns them as stops. More than 8 cities or 30
days is a clarify question, not a silent cut.

**Route step (new, deterministic).** Order is the traveller's order; it is
re-ordered only when they ask ("best order"), by nearest neighbour from the
origin. Nights not given are split evenly, remainder to earlier cities,
minimum one each. If the trip has fewer nights than cities, ask.

**Research per segment.** The existing attraction, hotel, weather and
stay-research agents run once per segment with that segment's geo and dates,
segments in parallel (cap 3 at a time). The 80 km place filter and 40 km
hotel filter measure from the segment's centre. Progress events name the
city.

**Between cities.** For each leg: a one-way flight search when both ends map
to an airport and the cities are more than 150 km apart; a Google Routes
transit and a drive estimate when they are under 900 km. Estimated options
get duration from Routes, no price unless Routes returns a fare,
`estimated=true`, and a link built from the two place names (Google Maps
directions). Rank by door-to-door time (flights add 2.5 h for airport time)
then price; the top option is selected.

**Composing.** `Planner.compose` runs per segment, so the LLM prompt holds
one city's candidates and days. The scheduler gets each day's available
window: a travel day starts after the selected leg arrives plus a buffer,
and ends before the next leg departs. Results are stitched in date order.
Day titles and meal fallbacks use the segment's city and stay.

**Profile effects (deterministic).**
- pace: stops per day 2 / 4 / 5 and longer or shorter gaps.
- with_children: boost family-suited place types, end the day by 19:00, cap
  a single transfer at 45 min.
- business: rank stays by distance to the centre and workspace amenities,
  one or two stops a day outside working hours unless told otherwise.
- step_free: prefer places Google marks wheelchair accessible, list the ones
  with no data under the assessment's `unverified`, prefer transit/drive
  transfers over long walks.

**Checks.** `refine_and_refresh`, `validate_plan` and `repair_plan` run per
segment (repair no longer sends the whole plan JSON). New whole-plan checks:
every night has one stay; every pair of consecutive segments has a leg with
a selected option; no stop is scheduled before arrival or after departure.
Assessment scores the whole plan and names the city in each finding.

**Limits.** Turn deadline, agent budget and tool-call cap scale with the
number of segments researched in the turn (base + per-segment allowance).
Hong Kong disruption handling keys off each segment.

## Editing by chat

- The override "a plan for one city is not moved to another" is replaced:
  naming an extra city adds a segment; replacing the only city, or all of
  them, plans anew.
- New modify operations: add city (position, nights), remove city, reorder,
  change nights of a city, shift trip dates, change or split a stay, choose
  a transport option. `modify.md` accepts `day_1..day_30` and segment ids.
- Untouched segments keep their days, stays and choices exactly. A changed
  segment is researched and composed again; legs touching a changed
  boundary are searched again. Locked and confirmed stops in a surviving
  segment are kept.
- `POST choose` takes `target_id` for hotel (stay id) and transport (leg
  id). The plan diff names the stay or leg that changed.

## Full-plan context (goal 7)

A new renderer `plan_detail(plan)` in `application/prompt_context.py` writes
the trip as text: per segment the city and dates, its stay(s) with price and
rating, the leg into it with the chosen option, then every day with each
item's id, time, name, type, price, rating and lock mark, then the cost
total. No URLs, no images.

- **No selected stop and no day named in the message:** modify, ask and the
  router get `plan_detail` for the whole plan.
- **A stop selected or a day named:** they get the short outline of the
  whole plan plus `plan_detail` for that day and its segment's stay, and the
  selected stop is named in the prompt (today it is applied only by rules
  afterwards).
- The map's active day is **not** treated as a selection: it is always set,
  so sending it would mean the full plan is never sent.
- Budget: 24 000 characters. Over that, drop in order: unchosen
  alternatives, meal detail, then per-item price/rating for the cities
  furthest from the one being discussed. Never drop a day or a stay.

This part does not depend on the multi-city model and ships first.

## Links, ranking, pictures

- **Links** are assembled in one place from research fields: place website →
  `official`; Google Maps URI or a URL built from place id / coordinates →
  `map`; hotel search link → `booking`; flight query URL → `booking`;
  estimated leg → `map` (directions).
- **Ranking**: one scoring function per kind over price against budget,
  rating and review count, distance (to the day's stops for meals, to the
  segment's stops for stays), profile fit and, for stays, the existing
  stay-research match. `reasons` are short phrases produced from the top
  contributing factors by code, in the plan's language through the existing
  i18n path.
- **Pictures**: Google Places photo references are served through a new
  backend route that adds the key server-side and caches; the plan stores
  only the route URL. Without a Google key, a Wikimedia page image is looked
  up by Wikidata/Wikivoyage title. Hotel thumbnails come from the SerpApi
  result. Attribution is stored and shown. A missing picture shows nothing.

## UI (`frontend/src`)

- **Trip form:** destination becomes an ordered list of cities, each with an
  optional nights field, add/remove/reorder, max 8. One city looks like
  today's field.
- **Header:** the route ("Tokyo → Kyoto → Osaka") and total dates; the clock
  follows the active day's city.
- **Workspace:** days grouped under a city heading showing dates and nights.
  Each city shows its stay card(s) and that stay's options (rank, reason,
  price per night and total, rating, distance, picture, links). Between
  cities, a transport card with the chosen option and the other options, any
  mode choosable, estimated ones labelled "estimate".
- **Day strip:** grouped and labelled by city.
- **Stop and meal cards:** picture, link row (Book / Website / Map), rank
  and reason on alternatives.
- **Map:** the active day fits to that day's city with that night's stay;
  travel days draw the leg; a trip overview shows every city and the legs
  between them. The 400 km filter and the Hong Kong fallback centre go.
- **Updates:** a refined plan highlights changed stops, the stay(s) that
  changed (a set of ids, not one flag) and changed legs.
- **Exports and versions:** Markdown, slides and the version list use the
  route and group by city.
- Storybook stories for a three-city plan, a split stay and a
  flight-versus-train leg. Pure helpers get `node:test` tests.

## Settled after review (my calls)

- **Budget.** One total budget stays. The selected transport options are
  costed first; what is left is shared per night, evenly, across cities, and
  that per-night share is what each city's hotel search, over-budget filter
  and ranking use. The pre-plan budget floor checks the total. A plan whose
  legs have no price (estimates) reserves nothing for them and says so in
  the cost notes.
- **Ground legs without transit data.** When Google Routes gives no transit
  route, or there is no Google key, the leg still gets one ground option
  from straight-line distance (the existing `estimate_leg`), with mode
  `ground`, labelled "rough estimate", a directions link, and an entry under
  the assessment's `unverified`. Modes are flight | train | bus | drive |
  ground; `ferry` is dropped (no source). A Routes transit result is `train`
  when its longest step is rail, else `bus`.
- **Times on estimated legs.** An estimated leg is assumed to leave at 09:00
  local. The travel day shows it as "about 09:00 – about 11:40 (estimate)"
  and the scheduler uses the assumed arrival plus buffer. A chosen flight
  uses its real times.
- **Crossing one city ↔ several.** Adding a second city replaces the
  round-trip option with one-way legs, searched again; going back to one
  city rebuilds the round trip. The traveller's flight choice is lost in
  both cases and the plan diff and the reply say so. Stays and days of
  surviving cities are kept.
- **Nights against dates at planning time.** Stated nights that add up to
  less than the date range: the extra nights go to the first city and the
  reply says so. More than the range, or both given in one message and in
  conflict: ask. Nights with no dates: the trip length is their sum.
- **Repeat cities and the way home.** A city may appear more than once
  (Tokyo → Kyoto → Tokyo); each visit is its own segment, and nothing is
  keyed by city name. A chat edit naming a city that appears twice asks
  which visit unless a selected stop or named day settles it. Research for a
  repeated city is reused for places and searched per visit for hotels. The
  trip returns to the origin by default; "ending in X" / "one way" drops the
  return leg, and the leg check allows that.
- **Profile in the UI.** The trip form gets a pace select and three toggles
  (travelling with children, business trip, step-free access), as well as
  being read from chat.

## Order of work

1. Full-plan context.
2. Model, loading of old plans, `hotels[0]` removal, per-leg selection —
   single-city behaviour unchanged, all existing tests green.
3. Understanding, route step, per-segment research and composing, legs.
4. Multi-city edits by chat.
5. UI: form, workspace, map, exports.
6. Links, ranking, pictures.
7. Profile effects.

Each step ends with `pytest -q` green in `backend/`, `ruff check` clean,
frontend `typecheck` and `build` clean, and from step 5 a check in the
running app (LOCAL_ADMIN mode) of a three-city plan and a single-city plan.

## Left to the builder

Names of functions and files, the exact scoring weights, the exact scaling
of deadlines, cache storage for pictures, CSS, and test layout.
