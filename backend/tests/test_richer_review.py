"""Review fixes for richer itineraries: diet checks on the source's own types, stale hotel options,
choice scope, plan versions on a choice, stable meal ids, reorder cost, cached room checks, change notes."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.application.refine as refine_module
import app.infrastructure.providers.hotels as hotels_module
from app.application.choices import apply_choice, normalise_hotel_options, stay_dates_match
from app.application.meals import choose_meals, source_facts, to_alternative
from app.application.refine import (
    CHANGE_PREFIX,
    CHECK_PREFIX,
    PREFERENCE,
    _better_order,
    _context,
    _ordered,
    _write_notes,
    evaluate_plan,
    refine_and_refresh,
    refine_plan,
)
from app.config import Settings
from app.domain.models import (
    CostBreakdown,
    DateRange,
    DayPlan,
    Destination,
    GeoPoint,
    HotelStay,
    Money,
    TicketOption,
    TripContext,
    TripPlan,
)
from app.infrastructure.providers.hotels import SerpApiHotelSource
from app.interface.api import create_app
from tests.test_plan_choices import DETAILS, GUEST, START, _plan as _choice_plan
from tests.test_refine import CONTEXT, KM, LAT, LNG, MONDAY, _alt, _good_day, _meal, _plan, _stop

VEGETARIAN = TripContext(session_id="s1", destination="Kyoto", preferences=["vegetarian"])


# --- 1. diet checks read the source's own types ---------------------------------


def test_a_meal_whose_source_types_say_vegetarian_is_not_flagged_or_swapped() -> None:
    items = _good_day()
    items[3] = _meal(
        "l", "Saravana Bhavan", "lunch", "12:30", "13:30", 0.9, cuisine="Indian",
        source_types=["indian_restaurant", "vegetarian_restaurant", "restaurant", "food"],
        alternatives=[_alt("alt_x", "Other Place", 0.9, cuisine="Vegetarian")],
    )
    plan = _plan(items)
    assert evaluate_plan(plan, VEGETARIAN) == []
    refined, _, _ = refine_plan(plan, VEGETARIAN)
    assert next(item for item in refined.days[0].items if item.id == "l").name == "Saravana Bhavan"


def test_a_meal_with_a_cuisine_but_no_source_types_is_not_judged() -> None:
    items = _good_day()
    items[3] = _meal("l", "Curry House", "lunch", "12:30", "13:30", 0.9, cuisine="Indian")
    assert evaluate_plan(_plan(items), VEGETARIAN) == []


def test_a_diet_flag_says_only_what_the_source_said_and_swaps_to_a_labelled_option() -> None:
    items = _good_day()
    items[3] = _meal(
        "l", "Grill House", "lunch", "12:30", "13:30", 0.9, cuisine="Steakhouse",
        source_types=["steak_house", "restaurant"],
        alternatives=[
            _alt("alt_unknown", "Plain Diner", 0.9),  # nothing says it serves the diet
            _alt("alt_veg", "Green Table", 0.95, source_types=["vegetarian_restaurant"]),
        ],
    )
    issues = evaluate_plan(_plan(items), VEGETARIAN)
    assert [issue.code for issue in issues] == [PREFERENCE]
    message = issues[0].message
    assert "not vegetarian" not in message and "Steakhouse" in message and "no vegetarian label" in message
    refined, _, _ = refine_plan(_plan(items), VEGETARIAN)
    assert next(item for item in refined.days[0].items if item.id == "l").name == "Green Table"


def test_the_source_types_and_summary_reach_the_meal_and_its_alternatives() -> None:
    row = {
        "id": "m1", "name": "Saravana Bhavan", "geo": {"lat": LAT, "lng": LNG}, "cuisine": "Indian",
        "tags": ["indian_restaurant", "vegetarian_restaurant"], "description": "South Indian vegetarian " + "x" * 600,
        "source_refs": [{"title": "Saravana Bhavan", "url": "https://maps.test/sb", "source": "google-places"}],
    }
    facts = source_facts(row)
    assert facts["source_types"] == ["indian_restaurant", "vegetarian_restaurant"]
    assert facts["source_summary"].startswith("South Indian vegetarian") and len(facts["source_summary"]) <= 400
    alternative = to_alternative(row, None)
    assert alternative is not None and alternative.source_types == facts["source_types"]


# --- 2. stale hotel options ------------------------------------------------------


def _moved_plan(session_id: str = "s1") -> TripPlan:
    """The choice plan after an edit moved the stay a week later (hotels[0] only, as the edit path does)."""
    plan = _choice_plan(session_id)
    later = 7
    plan.dates = DateRange(start=START + timedelta(days=later), end=START + timedelta(days=later + 2))
    plan.days = [day.model_copy(update={"date": day.date + timedelta(days=later)}) for day in plan.days]
    stay = plan.hotels[0].model_copy(
        update={"check_in": plan.dates.start, "check_out": plan.dates.end, "price_per_night": Money(amount=150, currency="USD")}
    )
    plan.hotels = [stay]
    plan.hotel_options = [option for option in plan.hotel_options if option.id != stay.id]  # dropped by the edit
    return plan


def test_the_normaliser_puts_the_stay_in_the_options_and_marks_old_dates() -> None:
    plan = _moved_plan()
    normalise_hotel_options(plan)
    assert plan.hotel_options[0].id == plan.hotels[0].id and plan.hotel_options[0].check_in == plan.hotels[0].check_in
    stale = [option for option in plan.hotel_options if not stay_dates_match(option, plan.hotels[0])]
    assert {option.id for option in stale} == {"hotel_h2", "hotel_h3"}


def test_a_stale_option_is_never_named_as_a_cheaper_stay() -> None:
    plan = _moved_plan()
    plan.cost = CostBreakdown(currency="USD", total=1500, budget=1000, status="over")
    issues = evaluate_plan(plan, CONTEXT)
    budget = next(issue for issue in issues if issue.code == "budget_over")
    assert "Hotel 3" not in budget.message  # USD 80 was the rate for another week


def test_choosing_a_stale_option_carries_the_dates_and_never_the_old_rate(settings: Settings) -> None:
    from app.application.planner import Planner
    from app.infrastructure.providers.fx import FxService

    plan = _moved_plan()
    planner = Planner(None, settings, FxService())
    out = asyncio.run(apply_choice(plan, kind="hotel", target_id=None, option_id="hotel_h3", planner=planner, context=None))
    stay = out.hotels[0]
    assert stay.id == "hotel_h3" and (stay.check_in, stay.check_out) == (plan.dates.start, plan.dates.end)
    assert stay.price_per_night is None and stay.room_types == [] and "not checked" in stay.notes.lower()
    assert not any(line.label.startswith("Hotel") for line in out.cost.lines)
    assert "80" not in json.dumps([line.model_dump(mode="json") for line in out.cost.lines])
    assert any("rate for these dates" in note.lower() for note in out.cost.notes)
    option = next(option for option in out.hotel_options if option.id == "hotel_h3")
    assert option.check_in == plan.dates.start and option.price_per_night is None


# --- 3. a choice refines only what it touched ---------------------------------------


def _two_day_plan() -> TripPlan:
    d1 = [_stop("a1", "A", "10:00", "11:00", 0)]
    d2 = [_stop("b1", "B1", "09:00", "10:00", 0), _stop("b2", "B2", "10:30", "11:30", 3),
          _stop("b3", "B3", "12:00", "13:00", 0.2), _stop("b4", "B4", "13:30", "14:30", 3.2)]
    tickets = [TicketOption(id=f"f{i}", name=f"F{i}", mode="flight", price=Money(amount=100 * i, currency="USD")) for i in (1, 2)]
    return TripPlan(
        session_id="s1", destination=Destination(name="Kyoto"), dates=DateRange(start=MONDAY, end=MONDAY + timedelta(days=1)),
        days=[DayPlan(date=MONDAY, items=d1), DayPlan(date=MONDAY + timedelta(days=1), items=d2)], tickets=tickets,
    )


def test_choosing_a_flight_only_checks_the_plan_and_moves_no_stop(settings: Settings) -> None:
    from app.application.planner import Planner
    from app.infrastructure.providers.fx import FxService

    plan = _two_day_plan()
    out = asyncio.run(apply_choice(plan, kind="flight", target_id=None, option_id="f2",
                                   planner=Planner(None, settings, FxService()), context=CONTEXT))
    assert [(item.id, item.start) for item in out.days[1].items] == [(item.id, item.start) for item in plan.days[1].items]
    assert any(note.startswith(CHECK_PREFIX) and "another order" in note for note in out.data_quality.notes)
    assert not any(note.startswith(CHANGE_PREFIX) for note in out.data_quality.notes)


def test_choosing_a_stop_refines_only_its_day_and_reports_each_change(settings: Settings) -> None:
    from app.application.planner import Planner
    from app.infrastructure.providers.fx import FxService

    plan = _two_day_plan()
    plan.days[0].items[0] = _stop("a1", "A", "10:00", "11:00", 0, alternatives=[_alt("alt_z", "Z", 0.1)])
    # Day 2's order is wasteful too, but the choice is on day 1: day 2 stays as it is.
    out = asyncio.run(apply_choice(plan, kind="item", target_id="a1", option_id="alt_z",
                                   planner=Planner(None, settings, FxService()), context=CONTEXT))
    assert [(item.id, item.start) for item in out.days[1].items] == [(item.id, item.start) for item in plan.days[1].items]
    assert out.days[0].items[0].name == "Z"


def test_a_change_the_loop_makes_after_a_choice_is_in_the_summary_and_a_note(settings: Settings) -> None:
    from app.application.planner import Planner
    from app.infrastructure.providers.fx import FxService

    plan = _two_day_plan()
    # The option is 10 km away: the next stop must start later to leave time for the transfer.
    plan.days[0].items = [
        _stop("a1", "A", "10:00", "11:00", 0, alternatives=[_alt("alt_far", "Far Z", 10.0)]),
        _stop("a2", "B", "11:05", "12:00", 0.1),
    ]
    out = asyncio.run(apply_choice(plan, kind="item", target_id="a1", option_id="alt_far",
                                   planner=Planner(None, settings, FxService()), context=CONTEXT))
    moved = next(item for item in out.days[0].items if item.id == "a2")
    assert moved.start > "11:05"
    # The swap's own transfer pushed B later: told in the summary, never silent.
    assert "B starts later to leave time for the transfer" in out.change_summary


def test_every_refine_change_is_a_plan_change_note_and_is_returned() -> None:
    items = [
        _stop("a1", "Temple", "09:00", "10:30", 0.0),
        _stop("a2", "Garden", "10:45", "12:00", 0.3),
        _meal("l", "Noodle House", "lunch", "15:00", "16:00", 0.4),
        _stop("a3", "Museum of Craft", "16:30", "18:00", 0.6),
    ]
    changes: list[str] = []
    refined, _, rounds = refine_plan(_plan(items), CONTEXT, changes=changes)
    assert rounds >= 1 and any("Noodle House" in change and "15:00" in change for change in changes)
    notes = [note for note in refined.data_quality.notes if note.startswith(CHANGE_PREFIX)]
    assert notes and all(f"{CHANGE_PREFIX}{change}" in notes for change in changes)


def test_a_retime_is_a_plan_change() -> None:
    items = _good_day()
    items[2] = _stop("a2", "Far Garden", "10:45", "12:15", 10.0)
    changes: list[str] = []
    refine_plan(_plan(items), CONTEXT, changes=changes)
    assert any("Far Garden 10:45 to" in change for change in changes)


def test_a_reorder_is_a_plan_change() -> None:
    from tests.test_refine import _zigzag

    changes: list[str] = []
    refine_plan(_plan(_zigzag()), CONTEXT, changes=changes)
    assert any("order" in change.lower() for change in changes)


# --- 4. a choice names the version it was made on ----------------------------------------


@pytest.fixture()
def client(settings: Settings, monkeypatch: pytest.MonkeyPatch):
    with TestClient(create_app(settings)) as test_client:
        gateway = test_client.app.state.container.gateway

        async def no_model(*_: Any, **__: Any):
            raise AssertionError("no model call")

        monkeypatch.setattr(gateway, "json", no_model)
        monkeypatch.setattr(gateway, "text", no_model)
        yield test_client


def _saved(client: TestClient, plan_factory=_choice_plan) -> tuple[str, TripPlan]:
    session_id = client.post("/api/journeys", json={"client_token": "guest-choose"}).json()["session"]["id"]
    plan = plan_factory(session_id)
    client.app.state.container.store.save_plan(plan, "test plan")
    return session_id, client.app.state.container.store.get_plan(session_id)


def test_a_choice_on_an_older_version_is_409(client: TestClient) -> None:
    session_id, plan = _saved(client)
    url = f"/api/journeys/{session_id}/plan/choose"
    stale = client.post(url, json={"kind": "flight", "option_id": "flight_f2", "plan_version": plan.version + 1}, headers=GUEST)
    assert stale.status_code == 409, stale.text
    assert client.app.state.container.store.get_plan(session_id).version == plan.version
    fresh = client.post(url, json={"kind": "flight", "option_id": "flight_f2", "plan_version": plan.version}, headers=GUEST)
    assert fresh.status_code == 200, fresh.text


# --- 5. meal ids follow the venue ---------------------------------------------------


def _venue(name: str, north: float, url: str) -> dict:
    return {
        "name": name, "geo": {"lat": LAT + north * KM, "lng": LNG}, "rating": 4.5, "review_count": 100,
        "source_refs": [{"title": name, "url": url, "source": "google-places"}],
    }


def test_a_meal_id_follows_the_venue_not_its_position() -> None:
    first, second = _venue("Alpha Ramen", 0.1, "https://maps.test/alpha"), _venue("Beta Soba", 0.2, "https://maps.test/beta")
    assignment = {0: [{"name": "Temple", "geo": {"lat": LAT, "lng": LNG}}]}

    def lunch(pool: list[dict]) -> dict:
        rows = [{"id": f"m{index + 1}", **row} for index, row in enumerate(pool)]
        return choose_meals(pool=rows, nearby={}, assignment=assignment, dates=[MONDAY], fallback_centre=None)[0]["lunch"]

    one, two = lunch([first, second]), lunch([dict(second, geo=first["geo"]), dict(first, geo=second["geo"])])
    assert one["name"] == "Alpha Ramen" and two["name"] == "Beta Soba"
    assert one["id"] != two["id"]  # a different venue never takes the id
    again = lunch([second, first])
    assert again["name"] == "Alpha Ramen" and again["id"] == one["id"]
    alt_one = to_alternative({"id": "m9", **first}, None)
    alt_two = to_alternative({"id": "m9", **second}, None)
    assert alt_one is not None and alt_two is not None and alt_one.id != alt_two.id


# --- 6. reordering stays cheap and off the event loop -----------------------------------


def test_long_runs_use_2_opt_not_permutations(monkeypatch: pytest.MonkeyPatch) -> None:
    real = refine_module.permutations

    def guarded(items):
        items = list(items)
        assert len(items) <= 5, "brute force past 5 stops"
        return real(items)

    monkeypatch.setattr(refine_module, "permutations", guarded)
    north = [0, 5, 1, 4, 2, 6, 3, 7]
    items = [_stop(f"s{k}", f"S{k}", f"{9 + k:02d}:00", f"{9 + k:02d}:45", north[k]) for k in range(8)]
    plan = _plan(items)
    ctx = _context(plan, CONTEXT, None, ())
    started = time.perf_counter()
    better = _better_order(plan.days[0], _ordered(plan.days[0]), ctx)
    assert time.perf_counter() - started < 0.5
    assert better is not None and better[3] < better[2]


def test_refine_and_refresh_runs_the_loop_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    real = refine_module.refine_plan

    def spy(*args: Any, **kwargs: Any):
        seen.append(threading.get_ident())
        return real(*args, **kwargs)

    monkeypatch.setattr(refine_module, "refine_plan", spy)
    asyncio.run(refine_and_refresh(_plan(_good_day()), CONTEXT))
    assert seen and seen[0] != threading.get_ident()


# --- 7. an empty room check is kept for its dates -------------------------------------


def test_an_empty_room_answer_is_kept_for_its_dates_and_asked_again_for_new_ones(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id, _ = _saved(client)
    container = client.app.state.container
    calls: list[dict] = []
    answer: dict = {}

    async def fake_request_json(*_: Any, **kwargs: Any) -> dict:
        calls.append(kwargs["params"])
        return json.loads(json.dumps(answer))

    monkeypatch.setattr(hotels_module, "request_json", fake_request_json)
    monkeypatch.setattr(container.registry, "hotels", [SerpApiHotelSource(container.settings.model_copy(update={"serpapi_key": "k"}))])
    url = f"/api/journeys/{session_id}/plan/hotels/hotel_h2/rooms"
    assert client.get(url, headers=GUEST).json() == {"rooms": []}
    assert client.get(url, headers=GUEST).json() == {"rooms": []}
    assert len(calls) == 1
    # The stay moves a week later (as an edit does): the option is asked again, with the stay's dates.
    plan = container.store.get_plan(session_id)
    moved = _moved_plan(session_id).model_copy(update={"version": plan.version, "hotel_options": plan.hotel_options})
    container.store.save_plan(moved, None, replace=True)
    answer.update(DETAILS)
    rooms = client.get(url, headers=GUEST).json()["rooms"]
    assert len(calls) == 2 and rooms
    assert calls[1]["check_in_date"] == moved.hotels[0].check_in.isoformat()
    assert calls[1]["check_out_date"] == moved.hotels[0].check_out.isoformat()


# --- 8. change notes do not pile up ------------------------------------------------


def test_change_notes_are_deduped_and_capped_at_the_latest_six() -> None:
    plan = _plan(_good_day())
    plan.data_quality.notes = ["A plain note."] + [f"{CHANGE_PREFIX}old {index}." for index in range(5)]
    _write_notes(plan, [], ["old 3.", "new 1.", "new 2.", "new 3."])
    changes = [note for note in plan.data_quality.notes if note.startswith(CHANGE_PREFIX)]
    assert len(changes) == 6 and len(set(changes)) == 6
    assert changes[-1] == f"{CHANGE_PREFIX}new 3." and f"{CHANGE_PREFIX}old 0." not in changes
    assert "A plain note." in plan.data_quality.notes
