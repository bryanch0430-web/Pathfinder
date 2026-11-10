"""The deterministic refine loop: checks, fixes, notes. No network and no model call."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import pytest

import app.application.planning as planning_module
import app.application.refine as refine_module
from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner
from app.application.planning import PlanPath
from app.application.prompts import PromptLibrary
from app.application.refine import (
    BUDGET_OVER,
    CHECK_PREFIX,
    CLOSED,
    DAY_HOURS,
    DAY_WALKING,
    INEFFICIENT_ORDER,
    MEAL_WINDOW,
    OUTSIDE_HOURS,
    PREFERENCE,
    TRAVEL_GAP,
    PlanIssue,
    RefineLimits,
    evaluate_plan,
    refine_plan,
)
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.models import (
    CostBreakdown,
    DateRange,
    DayPlan,
    Destination,
    EvalCase,
    GeoPoint,
    HotelStay,
    ItemType,
    Money,
    PlanAlternative,
    PlanItem,
    SourceRef,
    TicketOption,
    TripContext,
    TripPlan,
)
from app.eval_efficiency import case_context, cost_sandbox, ignore_event
from app.eval_metering import MeteredLLM
from app.infrastructure.observability import NoopTracer

MONDAY = date(2027, 4, 12)
LAT, LNG = 35.0, 135.76
#: About 1 km of latitude.
KM = 1 / 111.2


def _ref(name: str) -> SourceRef:
    return SourceRef(title=name, url=f"https://example.test/{name.replace(' ', '-')}", source="google-places")


def _stop(item_id: str, name: str, start: str, end: str, km_north: float = 0.0, **extra: Any) -> PlanItem:
    return PlanItem(
        id=item_id, type=ItemType.ATTRACTION, name=name, start=start, end=end,
        geo=GeoPoint(lat=LAT + km_north * KM, lng=LNG), source_refs=[_ref(name)], **extra,
    )


def _meal(item_id: str, name: str, slot: str, start: str, end: str, km_north: float = 0.0, **extra: Any) -> PlanItem:
    return PlanItem(
        id=item_id, type=ItemType.MEAL, name=name, start=start, end=end, meal_slot=slot, tags=["meal"],
        geo=GeoPoint(lat=LAT + km_north * KM, lng=LNG), source_refs=[_ref(name)], **extra,
    )


def _alt(alt_id: str, name: str, km_north: float = 0.0, **extra: Any) -> PlanAlternative:
    return PlanAlternative(
        id=alt_id, name=name, geo=GeoPoint(lat=LAT + km_north * KM, lng=LNG), source_refs=[_ref(name)], **extra
    )


def _plan(items: list[PlanItem], **extra: Any) -> TripPlan:
    return TripPlan(
        session_id="s1",
        destination=Destination(name="Kyoto"),
        dates=DateRange(start=MONDAY, end=MONDAY),
        days=[DayPlan(date=MONDAY, items=items)],
        **extra,
    )


def _good_day() -> list[PlanItem]:
    return [
        _meal("b", "Morning Cafe", "breakfast", "08:00", "08:45"),
        _stop("a1", "Temple", "09:00", "10:30", 0.3, opening_hours=["Monday: 9:00 AM – 5:00 PM"], hours_status="stated"),
        _stop("a2", "Garden", "10:45", "12:15", 0.6),
        _meal("l", "Noodle House", "lunch", "12:30", "13:30", 0.9),
        _stop("a3", "Museum of Craft", "13:45", "15:15", 1.2),
        _meal("d", "Izakaya", "dinner", "19:00", "20:15", 1.0),
    ]


CONTEXT = TripContext(session_id="s1", destination="Kyoto")


def _codes(issues: list[PlanIssue]) -> list[str]:
    return [issue.code for issue in issues]


# --- a good plan -------------------------------------------------------------


def test_a_good_plan_has_no_issue_and_refine_leaves_it_as_it_is() -> None:
    plan = _plan(_good_day())
    assert evaluate_plan(plan, CONTEXT) == []
    refined, issues, rounds = refine_plan(plan, CONTEXT)
    assert (issues, rounds) == ([], 0)
    assert refined.model_dump() == plan.model_dump()


def test_refine_is_idempotent() -> None:
    plan = _plan(
        _good_day(),
        cost=CostBreakdown(currency="USD", total=1500, budget=1000, status="over"),
    )
    once, issues, _ = refine_plan(plan, CONTEXT)
    twice, again, rounds = refine_plan(once, CONTEXT)
    assert _codes(issues) == _codes(again) == [BUDGET_OVER]
    assert rounds == 0
    assert twice.model_dump() == once.model_dump()


def test_refine_does_not_change_the_plan_it_is_given() -> None:
    items = _good_day()
    items[2] = _stop("a2", "Garden", "10:45", "12:15", 0.6, opening_hours=["Monday: Closed"],
                     alternatives=[_alt("alt1", "Moss Garden", 0.7, opening_hours=["Monday: 9:00 AM – 5:00 PM"])])
    plan = _plan(items)
    before = plan.model_dump()
    refine_plan(plan, CONTEXT)
    assert plan.model_dump() == before


# --- hours -------------------------------------------------------------------


def test_a_stop_closed_on_its_day_is_found() -> None:
    items = _good_day()
    items[2] = _stop("a2", "Garden", "10:45", "12:15", 0.6, opening_hours=["Monday: Closed"])
    issues = evaluate_plan(_plan(items), CONTEXT)
    assert _codes(issues) == [CLOSED]
    assert issues[0].item_ids == ("a2",) and issues[0].severity == "error" and "Garden" in issues[0].message


def test_a_visit_outside_the_stated_hours_is_found() -> None:
    items = _good_day()
    items[4] = _stop("a3", "Museum of Craft", "13:45", "15:15", 1.2, opening_hours=["Monday: 9:00 AM – 3:00 PM"])
    assert _codes(evaluate_plan(_plan(items), CONTEXT)) == [OUTSIDE_HOURS]


def test_a_closed_default_is_swapped_for_an_open_alternative() -> None:
    items = _good_day()
    items[2] = _stop(
        "a2", "Garden", "10:45", "12:15", 0.6, opening_hours=["Monday: Closed"],
        alternatives=[
            _alt("alt_shut", "Shut Garden", 0.6, opening_hours=["Monday: Closed"]),
            _alt("alt_open", "Moss Garden", 0.65, opening_hours=["Monday: 9:00 AM – 5:00 PM"], rating=4.5),
        ],
    )
    refined, issues, rounds = refine_plan(_plan(items), CONTEXT)
    swapped = next(item for item in refined.days[0].items if item.id == "a2")
    assert swapped.name == "Moss Garden"
    assert swapped.source_refs[0].title == "Moss Garden" and swapped.rating == 4.5
    assert {alt.name for alt in swapped.alternatives} == {"Garden", "Shut Garden"}
    assert rounds >= 1 and CLOSED not in _codes(issues)
    assert any("Moss Garden replaces Garden" in note for note in refined.data_quality.notes)


def test_a_closed_stop_with_no_open_alternative_becomes_a_note() -> None:
    items = _good_day()
    items[2] = _stop("a2", "Garden", "10:45", "12:15", 0.6, opening_hours=["Monday: Closed"],
                     alternatives=[_alt("alt_shut", "Shut Garden", 0.6, opening_hours=["Monday: Closed"])])
    refined, issues, _ = refine_plan(_plan(items), CONTEXT)
    assert _codes(issues) == [CLOSED]
    assert refined.days[0].items[2].name == "Garden"
    assert any(note.startswith(CHECK_PREFIX) and "Garden is closed" in note for note in refined.data_quality.notes)


def test_a_locked_closed_stop_is_never_swapped() -> None:
    items = _good_day()
    items[2] = _stop("a2", "Garden", "10:45", "12:15", 0.6, opening_hours=["Monday: Closed"], locked=True,
                     alternatives=[_alt("alt_open", "Moss Garden", 0.6, opening_hours=["Monday: 9:00 AM – 5:00 PM"])])
    refined, issues, _ = refine_plan(_plan(items), CONTEXT)
    assert refined.days[0].items[2].name == "Garden" and CLOSED in _codes(issues)


# --- transfers ---------------------------------------------------------------


def test_a_transfer_longer_than_the_gap_is_found_and_the_day_pushed_later() -> None:
    items = _good_day()
    # 10 km away: the transport model says metro, about 35 min, with 15 min left before it.
    items[2] = _stop("a2", "Far Garden", "10:45", "12:15", 10.0)
    plan = _plan(items)
    assert TRAVEL_GAP in _codes(evaluate_plan(plan, CONTEXT))
    refined, issues, _ = refine_plan(plan, CONTEXT)
    assert TRAVEL_GAP not in _codes(issues)
    moved = next(item for item in refined.days[0].items if item.id == "a2")
    assert moved.start > "10:45"


# --- order -------------------------------------------------------------------


def _zigzag(**p_extra: Any) -> list[PlanItem]:
    return [
        _stop("x", "First Shrine", "09:00", "10:30", 0.0),
        _stop("p", "Far Hall", "11:00", "12:30", 2.0, **p_extra),
        _stop("q", "Near Hall", "13:00", "14:30", 0.2),
        _stop("r", "Far Tower", "15:00", "16:30", 2.2),
    ]


def test_an_inefficient_order_is_found_and_reordered() -> None:
    plan = _plan(_zigzag())
    issues = evaluate_plan(plan, CONTEXT)
    assert _codes(issues) == [INEFFICIENT_ORDER]
    refined, remaining, _ = refine_plan(plan, CONTEXT)
    ordered = sorted(refined.days[0].items, key=lambda item: item.start)
    assert [item.id for item in ordered] == ["x", "q", "p", "r"]
    assert ordered[0].start == "09:00"
    assert remaining == []


def test_a_shorter_order_that_breaks_opening_hours_is_not_offered() -> None:
    plan = _plan(_zigzag(opening_hours=["Monday: 9:00 AM – 12:30 PM"]))
    assert evaluate_plan(plan, CONTEXT) == []


# --- meals -------------------------------------------------------------------


def test_a_meal_outside_its_window_is_found_and_moved_inside_it() -> None:
    items = [
        _stop("a1", "Temple", "09:00", "10:30", 0.0),
        _stop("a2", "Garden", "10:45", "12:00", 0.3),
        _meal("l", "Noodle House", "lunch", "15:00", "16:00", 0.4),
        _stop("a3", "Museum of Craft", "16:30", "18:00", 0.6),
    ]
    plan = _plan(items)
    assert _codes(evaluate_plan(plan, CONTEXT)) == [MEAL_WINDOW]
    refined, issues, _ = refine_plan(plan, CONTEXT)
    lunch = next(item for item in refined.days[0].items if item.id == "l")
    assert "11:30" <= lunch.start <= "14:30" and issues == []


# --- day load ----------------------------------------------------------------


def _long_day() -> list[PlanItem]:
    times = [("08:00", "09:40"), ("09:50", "11:30"), ("11:40", "13:20"), ("13:30", "15:10"), ("15:20", "17:00"), ("17:10", "18:50")]
    ratings = [4.6, 1.0, 4.5, 3.0, 4.4, 4.3]
    items = [
        _stop(f"s{index}", f"Sight {index}", start, end, 0.3 * index, rating=ratings[index], review_count=1000)
        for index, (start, end) in enumerate(times)
    ]
    items[1].locked = True  # the lowest value, but the traveller fixed it
    return items


def test_an_overloaded_day_is_trimmed_but_locked_stops_stay() -> None:
    plan = _plan(_long_day())
    assert _codes(evaluate_plan(plan, CONTEXT)) == [DAY_HOURS]
    refined, issues, _ = refine_plan(plan, CONTEXT)
    names = [item.name for item in refined.days[0].items]
    assert "Sight 1" in names  # locked
    assert "Sight 3" not in names  # the weakest movable stop
    assert len(names) == 5
    hosts = [item for item in refined.days[0].items if any(alt.name == "Sight 3" for alt in item.alternatives)]
    assert len(hosts) == 1 and hosts[0].name in {"Sight 2", "Sight 4"}
    assert DAY_HOURS not in _codes(issues)


def test_a_requested_stop_is_never_trimmed() -> None:
    context = TripContext(session_id="s1", destination="Kyoto", preferences=["must-see: Sight 3"])
    refined, _, _ = refine_plan(_plan(_long_day()), context)
    names = [item.name for item in refined.days[0].items]
    assert "Sight 3" in names and "Sight 1" in names and len(names) == 5


def test_walking_km_past_the_limit_is_found() -> None:
    plan = _plan(_good_day())
    assert evaluate_plan(plan, CONTEXT) == []
    issues = evaluate_plan(plan, CONTEXT, limits=RefineLimits(max_walking_km=1.0))
    assert DAY_WALKING in _codes(issues)


# --- budget ------------------------------------------------------------------


def _stay(stay_id: str, name: str, rate: float) -> HotelStay:
    return HotelStay(
        id=stay_id, name=name, check_in=MONDAY, check_out=MONDAY,
        price_per_night=Money(amount=rate, currency="USD"), source_refs=[_ref(name)],
    )


def test_a_budget_overrun_names_the_cheaper_options_and_switches_nothing() -> None:
    dear, cheap = _stay("h1", "Grand Hotel", 300), _stay("h2", "Budget Inn", 120)
    flights = [
        TicketOption(id="t1", mode="flight", name="Dear Air 1", price=Money(amount=900, currency="USD"), selected=True),
        TicketOption(id="t2", mode="flight", name="Cheap Air 2", price=Money(amount=500, currency="USD")),
    ]
    plan = _plan(
        _good_day(), hotels=[dear], hotel_options=[dear, cheap], tickets=flights,
        cost=CostBreakdown(currency="USD", total=1500, budget=1000, status="over"),
    )
    refined, issues, rounds = refine_plan(plan, CONTEXT)
    assert _codes(issues) == [BUDGET_OVER] and rounds == 0
    assert refined.hotels[0].name == "Grand Hotel"
    assert [ticket.selected for ticket in refined.tickets] == [True, False]
    note = next(note for note in refined.data_quality.notes if note.startswith(CHECK_PREFIX))
    assert "Budget Inn" in note and "Cheap Air 2" in note and "500 over" in note


def test_a_plan_within_budget_has_no_budget_issue() -> None:
    plan = _plan(_good_day(), cost=CostBreakdown(currency="USD", total=900, budget=1000, status="within"))
    assert evaluate_plan(plan, CONTEXT) == []


# --- preferences -------------------------------------------------------------


def test_a_meal_that_conflicts_with_the_diet_is_swapped_for_one_that_suits_it() -> None:
    context = TripContext(session_id="s1", destination="Kyoto", preferences=["vegetarian"])
    items = _good_day()
    items[3] = _meal(
        "l", "Grill House", "lunch", "12:30", "13:30", 0.9, cuisine="Steakhouse",
        alternatives=[_alt("alt_veg", "Green Table", 0.9, cuisine="Vegetarian")],
    )
    plan = _plan(items)
    assert _codes(evaluate_plan(plan, context)) == [PREFERENCE]
    refined, issues, _ = refine_plan(plan, context)
    assert next(item for item in refined.days[0].items if item.id == "l").name == "Green Table"
    assert issues == []


def test_a_meal_that_suits_the_diet_or_states_no_cuisine_is_not_flagged() -> None:
    context = TripContext(session_id="s1", destination="Kyoto", preferences=["vegetarian"])
    items = _good_day()
    items[3] = _meal("l", "Green Bowl", "lunch", "12:30", "13:30", 0.9, cuisine="Vegan")
    assert evaluate_plan(_plan(items), context) == []  # breakfast and dinner state no cuisine


def test_a_stop_the_trip_asks_to_avoid_is_flagged() -> None:
    context = TripContext(session_id="s1", destination="Kyoto", preferences=["avoid museums", "no more than 4 stops"])
    issues = evaluate_plan(_plan(_good_day()), context)
    assert _codes(issues) == [PREFERENCE] and issues[0].item_ids == ("a3",)


# --- the loop ----------------------------------------------------------------


def test_the_loop_stops_at_max_rounds(monkeypatch: pytest.MonkeyPatch) -> None:
    stuck = [PlanIssue(TRAVEL_GAP, 0, ("a1", "a2"), "error", "Stuck.")]
    calls: list[int] = []
    monkeypatch.setattr(refine_module, "evaluate_plan", lambda *_, **__: list(stuck))
    monkeypatch.setattr(refine_module, "_apply_fixes", lambda *_, **__: calls.append(1) or True)
    _, issues, rounds = refine_plan(_plan(_good_day()), CONTEXT, max_rounds=3)
    assert rounds == 3 and len(calls) == 3 and _codes(issues) == [TRAVEL_GAP]
    _, _, rounds = refine_plan(_plan(_good_day()), CONTEXT, max_rounds=1)
    assert rounds == 1


# --- the plan path -----------------------------------------------------------


def _run_plan_path(settings: Settings) -> tuple[TripPlan, MeteredLLM]:
    llm = MeteredLLM()
    registry = cost_sandbox(settings, "Kyoto")
    gateway = LLMGateway(llm, PromptLibrary(settings), NoopTracer(), settings)
    plan_path = PlanPath(planner=Planner(gateway, settings, registry.fx), registry=registry, settings=settings)
    context = case_context(EvalCase(id="r", dataset="efficiency", input="Plan Kyoto", destination="Kyoto"), "s-refine")
    plan = asyncio.run(
        plan_path.run(
            context=context, session_id=context.session_id,
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="refine"),
            tracer=NoopTracer(), emit=ignore_event, version=1,
        )
    )
    return plan, llm


def test_the_plan_path_runs_refine_and_makes_no_extra_model_call(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []
    original = planning_module.refine_and_refresh

    async def spy(*args: Any, **kwargs: Any):
        result = await original(*args, **kwargs)
        seen.append(result[2])
        return result

    monkeypatch.setattr(planning_module, "refine_and_refresh", spy)
    _, with_refine = _run_plan_path(settings)
    _, without = _run_plan_path(settings.model_copy(update={"refine_max_rounds": 0}))
    assert len(seen) == 2
    assert len(with_refine.purposes) == len(without.purposes) >= 1
