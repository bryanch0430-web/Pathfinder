# Segments, Stays and Legs Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The plan model can hold several cities, a stay per night and a transport leg per hop with its own chosen option, while every single-city plan (new or stored) behaves exactly as today.

**Architecture:** Additive fields on the pydantic models with defaults, filled in by the `TripPlan` validator so old stored JSON loads unchanged. All positional reads of `hotels[0]` go through named lookups on the model. Ticket selection becomes per leg; a single-city plan has one leg, so its behaviour is the same.

**Tech Stack:** Python, pydantic v2, pytest. Backend only.

**Spec:** `docs/superpowers/specs/2026-11-12-multi-city-trips-design.md`, sections "Model" and "Settled after review". This is step 2 of its "Order of work".

## Global Constraints

- A single-city plan, new or loaded from storage, produces the same API output as before apart from the new fields.
- A plan stored before this change loads without error and gets the same generated ids on every load (`seg_1`, `leg_out`).
- No code outside `app/domain/models.py` indexes `hotels` by position.
- Max 8 stops on the trip form.
- All commands run from `backend/`: tests `PYTHONPATH=. python -m pytest -q -o addopts=""`, lint `ruff check`. Baseline: 9 known failing tests (see Task 0 of the step 1 plan), 532 ruff findings.
- Existing, unformatted backend files are changed by script or `sed`, not the Edit tool (the edit hook reformats whole files).
- Commit messages: one plain sentence, no attribution lines.
- `contracts/trip_plan.schema.json` is regenerated with `PYTHONPATH=backend python -m app.export_contracts` (from the repo root) in the task that changes the model.

**Deferred to later steps (not in this plan):** `Link`, ranking fields and `ImageRef` (step 6); `TravellerProfile` (step 7); `max_days` 14 → 30, stay-coverage and leg checks in `validate_plan` (step 3); the plan diff naming the stay or leg (step 4); frontend types (step 5).

**Ruling on transport modes:** the spec lists flight | train | bus | drive | ground and drops `ferry`. Stored plans may hold `ferry` and `ticket`, and they must still load, so the type keeps both and adds `drive` and `ground`.

## Review Focus

1. A stored plan whose single stay's dates no longer match the trip dates (an old edit moved one and not the other): `stay_on` still returns that stay for every day.
2. A date edit that changes `plan.dates` in place on a single-city plan: `segment_of` still answers, and the segment's dates follow the trip on the next validation.
3. A multi-city plan with no stay for one night: `stay_on` returns `None` and nothing crashes.
4. Choosing a transport option on one leg leaves every other leg's choice as it was.
5. A stored plan with tickets but no `selected` mark and no legs: one outbound leg, first option selected.

## File Structure

- Modify `backend/app/domain/models.py`: new models, fields, validator, lookups.
- Modify the 12 files that read `hotels[0]` (list from `grep -rn "\.hotels\[0\]" app`).
- Modify `backend/app/application/choices.py`, `backend/app/interface/api.py`, `backend/app/application/prompt_context.py`.
- Create `backend/tests/test_plan_segments.py`, `backend/tests/test_transport_legs.py`, `backend/tests/test_trip_stops.py`; extend `backend/tests/plan_samples.py`.

---

### Task 1: Segments and stay lookups

**Files:** `backend/app/domain/models.py`, `backend/tests/test_plan_segments.py`, `backend/tests/plan_samples.py`, `contracts/trip_plan.schema.json`

**Interfaces — Produces:**
- `class TripSegment(BaseModel)`: `id: str = new_id("seg")`, `destination: Destination`, `dates: DateRange` (arrival day .. departure day).
- `DayPlan.segment_id: str = ""`, `HotelStay.segment_id: str = ""`.
- `TripPlan.segments: list[TripSegment] = []`.
- `TripPlan` validator (in `_shape`, before the existing checks return):
  - no segments → `[TripSegment(id="seg_1", destination=self.destination, dates=self.dates)]`;
  - exactly one segment → its `destination` and `dates` are set from the plan's (the plan's own fields define a single-city trip);
  - more than one → ordered, `segments[0].dates.start == dates.start`, `segments[-1].dates.end == dates.end`, and each `segments[i].dates.end == segments[i+1].dates.start`, else `ValueError("segments must cover the trip in order, each starting the day the one before ends")`; `destination` must equal `segments[0].destination` by name, else `ValueError`;
  - every day with an empty or unknown `segment_id` gets `segment_of(day.date).id`; every stay with an empty or unknown `segment_id` gets `segment_of(stay.check_in).id`.
- `TripPlan.segment_of(day: date) -> TripSegment`: one segment → that one, whatever the date; otherwise the last segment whose `dates.start <= day`, else the first.
- `TripPlan.stay_on(day: date) -> HotelStay | None`: the stay with `check_in <= day < check_out`; else the stay with `check_out == day` (the last morning); else the only stay when the plan has exactly one stay and one segment; else `None`.
- `TripPlan.stays_in(segment_id: str) -> list[HotelStay]` in date order.
- `TripPlan.first_stay` (property) `-> HotelStay | None`: the first chosen stay. This is the named seam for code that still thinks of one stay per trip; steps 3 and 4 revisit each caller.
- `plan_samples.make_two_city_plan() -> TripPlan`: Kyoto 3–5 Dec 2026 (`seg_a`), Osaka 5–7 Dec (`seg_b`), days 3..7 Dec, stay `hotel_a` 3→5, stay `hotel_b` 5→7.

- [ ] **Step 1: failing tests** in `test_plan_segments.py`:

```python
def test_a_new_single_city_plan_gets_one_segment():
    plan = make_plan()
    assert [(s.id, s.destination.name, s.dates) for s in plan.segments] == [("seg_1", "Kyoto", plan.dates)]
    assert {day.segment_id for day in plan.days} == {"seg_1"} and plan.hotels[0].segment_id == "seg_1"

def test_a_plan_stored_before_segments_loads_the_same_every_time():
    old = make_plan().model_dump(mode="json")
    old.pop("segments"); [d.pop("segment_id") for d in old["days"]]; [h.pop("segment_id") for h in old["hotels"]]
    first, second = TripPlan.model_validate(old), TripPlan.model_validate(old)
    assert first.segments[0].id == second.segments[0].id == "seg_1"
    assert first.model_dump(exclude={"created_at", "updated_at"}) == second.model_dump(exclude={"created_at", "updated_at"})

def test_a_single_segment_follows_the_trip_dates_and_destination():
    data = make_plan().model_dump(mode="json")
    data["segments"][0]["dates"] = {"start": "2020-01-01", "end": "2020-01-02"}
    assert TripPlan.model_validate(data).segments[0].dates == make_plan().dates

def test_the_day_two_cities_share_belongs_to_the_city_arrived_in():
    plan = make_two_city_plan()
    assert [plan.segment_of(day.date).id for day in plan.days] == ["seg_a", "seg_a", "seg_b", "seg_b", "seg_b"]
    assert [day.segment_id for day in plan.days] == ["seg_a", "seg_a", "seg_b", "seg_b", "seg_b"]

def test_the_stay_of_each_night_and_of_the_last_morning():
    plan = make_two_city_plan()
    assert [plan.stay_on(day.date).id for day in plan.days] == ["hotel_a", "hotel_a", "hotel_b", "hotel_b", "hotel_b"]
    assert [s.id for s in plan.stays_in("seg_b")] == ["hotel_b"] and plan.first_stay.id == "hotel_a"

def test_a_night_with_no_stay_has_none():
    plan = make_two_city_plan(); plan.hotels = plan.hotels[:1]
    assert plan.stay_on(date(2026, 12, 6)) is None

def test_a_single_stay_whose_dates_drifted_is_still_the_stay():
    plan = make_plan(); plan.hotels[0].check_in = date(2026, 11, 1); plan.hotels[0].check_out = date(2026, 11, 2)
    assert plan.stay_on(plan.days[0].date).id == "hotel_1"

def test_a_plan_with_no_stay(): assert make_plan(with_stay=False).first_stay is None and make_plan(with_stay=False).stay_on(START) is None

def test_segments_must_cover_the_trip_in_order():   # gap between segments, wrong first start, destination != first segment
    for broken in (gap, late_start, other_destination):
        with pytest.raises(ValidationError): TripPlan.model_validate(broken)
```

- [ ] **Step 2:** run `tests/test_plan_segments.py`; expected: FAIL (no `segments`).
- [ ] **Step 3:** implement in `models.py`.
- [ ] **Step 4:** run the file (PASS), then regenerate the contract and run `tests/test_export_contracts.py` (PASS).
- [ ] **Step 5:** commit: `A plan is made of one or more city segments, and the stay of any night is looked up by its date`.

### Task 2: Stops on the trip form

**Files:** `backend/app/domain/models.py`, `backend/tests/test_trip_stops.py`

**Interfaces — Produces:**
- `TRIP_STOPS_MAX = 8`.
- `class TripStop(BaseModel)`: `city: str` (1..`TRIP_FIELD_MAX_LENGTH` characters, stripped), `nights: int | None` (1..60).
- `TripContext.stops: list[TripStop] = []`, at most `TRIP_STOPS_MAX` (a ninth is a `ValidationError`).
- `TripContext.route` (property) `-> list[TripStop]`: `stops` when set, else one stop for `destination` when it is set, else `[]`.

- [ ] **Step 1: failing tests:** a context stored without `stops` loads with `stops == []` and `route == [TripStop(city=destination)]`; two stops round-trip through `model_dump`/`model_validate`; nine stops, `nights=0` and an empty city each raise `ValidationError`; `route == []` with no destination.
- [ ] **Step 2:** run; FAIL. **Step 3:** implement. **Step 4:** run; PASS; run `tests -k "context or state or journey"`; PASS.
- [ ] **Step 5:** commit: `The trip form can hold up to eight cities in order, each with its nights`.

### Task 3: No positional hotel reads

**Files:** the 12 files from `grep -rln "\.hotels\[0\]" app`; `backend/tests/test_plan_segments.py`

- [ ] **Step 1: failing test:**

```python
def test_no_code_outside_the_model_reads_the_hotels_by_position():
    offenders = [f"{p}:{n}" for p in Path("app").rglob("*.py") if p.name != "models.py"
                 for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
                 if re.search(r"\.hotels\[\d", line.split("#")[0]) and "``" not in line]
    assert offenders == []
```

- [ ] **Step 2:** run; FAIL listing the sites.
- [ ] **Step 3:** by script over `app/`: `X.hotels[0] if X.hotels else None` → `X.first_stay`; every remaining `X.hotels[0]` → `X.first_stay`; `choices.py:253` `[chosen, *updated.hotels[1:]]` is rewritten in Task 5 (for now `[chosen if stay is current else stay for stay in updated.hotels] or [chosen]`). Where a day is in scope the lookup is `stay_on(day.date)` instead: check `asking.py` (`_reference_point`, the distance facts), `refine.py:274`, `modification.py:1233` by reading each, and use `stay_on` only where the code is already working on one day.
- [ ] **Step 4:** run the test (PASS) and the full suite (no failure outside the baseline).
- [ ] **Step 5:** commit: `Code that needs the trip's stay asks the plan for it by name or by date`.

### Task 4: One chosen transport option per leg

**Files:** `backend/app/domain/models.py`, `backend/app/application/choices.py`, `backend/app/application/prompt_context.py`, `backend/tests/test_transport_legs.py`, contract

**Interfaces — Produces:**
- `class TransportLeg(BaseModel)`: `id: str = new_id("leg")`, `from_place: str = ""`, `to_place: str = ""`, `depart_date: date | None = None` (not `date`: a field of that name with a default hides the `date` type inside the class), `kind: Literal["outbound", "between", "return"] = "outbound"`.
- `TicketOption.leg_id: str = ""`, `TicketOption.estimated: bool = False`, `mode: Literal["flight", "train", "bus", "drive", "ground", "ticket", "ferry"]`.
- `TripPlan.legs: list[TransportLeg] = []`.
- Validator: tickets and no legs → `[TransportLeg(id="leg_out", from_place=self.origin, to_place=self.destination.name, depart_date=self.dates.start, kind="outbound")]`; a ticket with an empty or unknown `leg_id` gets `legs[0].id`; exactly one option is selected in each leg (the first marked, else the first); leg ids unique.
- `selected_ticket(tickets, leg_id: str | None = None) -> TicketOption | None`: among the options of `leg_id`, or of the first ticket's leg when `leg_id` is None. Existing callers are unchanged.
- `TripPlan.options_for(leg_id) -> list[TicketOption]`, `TripPlan.chosen_tickets() -> list[TicketOption]` (one per leg, in leg order; legs with no options are skipped).
- `choices._choose_flight`: selects within the option's own leg only; its "before" is that leg's chosen option.
- `plan_detail`: one `Transport:` line per leg from `chosen_tickets()`, each followed by that leg's `Other transport:` line.
- `plan_samples.make_two_city_plan()` gains legs `leg_out` (HKG→Kyoto, flights `t_out_1` selected, `t_out_2`), `leg_ab` (Kyoto→Osaka, `t_ab_train` mode train `estimated=True`, `t_ab_bus`), `leg_home` (`t_home_1`).

- [ ] **Step 1: failing tests:** a single-city plan has `[("leg_out", "outbound")]` and every ticket's `leg_id == "leg_out"`; a stored plan without `legs`/`leg_id`/`selected` loads with `leg_out` and its first option selected, the same on every load; the two-city plan has exactly one selected option in each of its three legs; `selected_ticket(plan.tickets)` is `t_out_1` and `selected_ticket(plan.tickets, "leg_ab")` is `t_ab_train`; `_choose_flight(plan, "t_ab_bus")` selects it and leaves `t_out_1` and `t_home_1` selected; `mode` accepts `drive`, `ground`, `ferry`, `ticket`; `plan_detail(two_city)` has three `Transport:` lines and names `t_ab_train`; duplicate leg ids raise.
- [ ] **Step 2:** run; FAIL. **Step 3:** implement. **Step 4:** run; PASS; regenerate the contract; run `tests -k "choice or flight or ticket or contract or plan_detail"`; PASS.
- [ ] **Step 5:** commit: `Each hop of a trip is a leg with its own transport options, and one is chosen per leg`.

### Task 5: The choose API names the stay or the leg

**Files:** `backend/app/application/choices.py`, `backend/app/interface/api.py`, `backend/tests/test_transport_legs.py`, `backend/tests/test_plan_segments.py`

**Interfaces — Produces:**
- `ChoiceRequest.kind: Literal["item", "hotel", "flight", "transport"]`; `transport` is handled as `flight` is.
- `apply_choice(..., kind, target_id, option_id, ...)`:
  - `hotel`: `target_id` is the id of the stay being replaced. None → the stay of the option's segment (`stays_in(option.segment_id)[0]`), else `first_stay`. A `target_id` that is not a chosen stay → `ChoiceNotFound("stay not found")`. The chosen option takes the target stay's `check_in`, `check_out` and `segment_id` and replaces it in place; other stays are untouched. The locked/confirmed refusal and the unchecked-rate rule apply to the target stay.
  - `flight`/`transport`: `target_id`, when given, must be the option's `leg_id`, else `ChoiceNotFound("transport option not found on that leg")`.
- `normalise_hotel_options`: every chosen stay is among the options as it stands now (its own copy replaces the option with its id; a chosen stay missing from the options is added), with `first_stay` first as today.

- [ ] **Step 1: failing tests:** on the two-city plan with options `opt_b2` (segment `seg_b`, dates 5→7): `apply_choice(kind="hotel", target_id="hotel_b", option_id="opt_b2")` gives stays `["hotel_a", "opt_b2"]` with `opt_b2` 5→7 in `seg_b`; the same with `target_id=None` gives the same result; `target_id="nope"` raises `ChoiceNotFound`; `kind="transport", target_id="leg_ab", option_id="t_ab_bus"` selects it; `target_id="leg_out"` with `t_ab_bus` raises `ChoiceNotFound`; single-city `kind="hotel", target_id=None` behaves as `tests/test_hotel_swap_choice.py` expects (run that file).
- [ ] **Step 2:** run; FAIL. **Step 3:** implement. **Step 4:** run; PASS; run `tests/test_hotel_swap_choice.py tests/test_plan_choices.py tests/test_frontend_routes.py`; PASS.
- [ ] **Step 5:** commit: `A choice of stay names the stay it replaces and a choice of transport names its leg`.

### Task 6: Whole-suite check

- [ ] Full suite: no failing test outside the 9-test baseline. `ruff check`: no more than 532 findings and none in new files. `git diff --stat main..HEAD -- backend/app` shows no whole-file reformatting of pre-existing files.
