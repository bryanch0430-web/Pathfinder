"""A day, a clock and a count are different numbers, and a missing fare is not a pass."""

from __future__ import annotations

from datetime import date

from app.application.modification import answer_plan_question, apply_plan_shape, interpret_plan
from app.application.planner import Planner, _constraints
from app.application.router import RuleRouter, affected_parts_for, rules_decide_alone
from app.domain.models import (
    ConstraintStatus,
    DateRange,
    DayPlan,
    Destination,
    HotelStay,
    ItemType,
    PlanItem,
    SourceRef,
    TicketOption,
    TripContext,
    TripPlan,
)
from app.infrastructure.providers.fx import FxService

DAY2 = "only change day 2 to one attraction, keep the other days the same"
AFTERNOON = "day 2 starts after 14:00, only one attraction"
COMPOUND = (
    "travelling with an elderly person, less walking, change lunch to vegetarian, keep the budget the same"
)
QUESTION = "Could day 2 be one attraction and the other days stay the same?"


def _attraction(name: str, start: str, end: str) -> PlanItem:
    return PlanItem(
        id=name.lower().replace(" ", "_"),
        type=ItemType.ATTRACTION,
        name=name,
        start=start,
        end=end,
        source_refs=[SourceRef(title=name, source="check")],
    )


def _meal(name: str, start: str, end: str, slot: str, cuisine: str = "") -> PlanItem:
    return PlanItem(
        id=name.lower().replace(" ", "_"),
        type=ItemType.MEAL,
        name=name,
        start=start,
        end=end,
        meal_slot=slot,  # type: ignore[arg-type]
        cuisine=cuisine,
    )


def _plan() -> TripPlan:
    start = date(2026, 11, 1)
    slots = [("09:00", "09:40"), ("10:00", "10:40"), ("11:00", "11:40")]
    days = []
    for index, count in enumerate((3, 3, 2)):
        items = [_attraction(f"D{index + 1} place {n + 1}", *slots[n]) for n in range(count)]
        items.append(_meal(f"D{index + 1} lunch", "12:00", "12:40", "lunch"))
        items.append(_meal(f"D{index + 1} dinner", "18:00", "18:40", "dinner", "noodles"))
        days.append(DayPlan(date=date.fromordinal(start.toordinal() + index), items=items))
    return TripPlan(
        session_id="verify",
        destination=Destination(name="Kyoto"),
        origin="Hong Kong",
        dates=DateRange(start=start, end=date(2026, 11, 3)),
        days=days,
        hotels=[HotelStay(id="hotel_1", name="Sample Hotel", check_in=start, check_out=date(2026, 11, 4))],
        tickets=[TicketOption(id="flight_1", mode="flight", name="Sample Flight", selected=True, leg_id="leg_out")],
    )


def _counts(plan: TripPlan) -> list[int]:
    return [sum(item.type is ItemType.ATTRACTION for item in day.items) for day in plan.days]


def _clock(plan: TripPlan) -> list[list[str]]:
    return [
        [f"{item.start}-{item.end}" for item in day.items if item.type is ItemType.ATTRACTION]
        for day in plan.days
    ]


def test_only_day_2_changes_to_one_attraction() -> None:
    shape = interpret_plan(DAY2)
    plan = _plan()

    assert shape.places_per_day == 1 and shape.days == [2] and shape.reason == ""
    apply_plan_shape(plan, shape)

    assert _counts(plan) == [3, 1, 2]


def test_a_model_count_of_one_still_stays_on_day_2() -> None:
    shape = interpret_plan(DAY2, {"places_per_day": 1})
    plan = _plan()
    apply_plan_shape(plan, shape)

    assert shape.days == [2] and _counts(plan) == [3, 1, 2]


def test_an_afternoon_start_and_one_attraction_stay_on_day_2() -> None:
    shape = interpret_plan(AFTERNOON)
    plan = _plan()
    before = _clock(plan)
    apply_plan_shape(plan, shape)
    after = _clock(plan)

    assert shape.places_per_day == 1 and shape.earliest == "14:00" and shape.days == [2]
    assert _counts(plan) == [3, 1, 2]
    assert after[0] == before[0] and after[2] == before[2]
    assert after[1] == ["14:00-14:40"]


def test_a_clock_hour_from_the_model_does_not_block_the_start_time() -> None:
    shape = interpret_plan("day 2 starts after 14:00", {"places_per_day": 14})
    plan = _plan()
    before = _clock(plan)
    summary = apply_plan_shape(plan, shape)

    assert shape.places_per_day is None and shape.earliest == "14:00" and shape.reason == ""
    assert "14" in shape.count_note
    assert _counts(plan) == [3, 3, 2]
    assert _clock(plan)[0] == before[0] and _clock(plan)[2] == before[2]
    assert _clock(plan)[1][0].startswith("14:00")
    assert "not changed" not in summary


def test_less_walking_and_a_vegetarian_lunch_leave_the_budget_alone() -> None:
    shape = interpret_plan(COMPOUND)
    plan = _plan()
    summary = apply_plan_shape(plan, shape)

    assert shape.keep_budget and shape.diet == "vegetarian" and shape.diet_meal == "lunch"
    assert shape.pace == "less_walking" and shape.places_per_day == 2
    assert _counts(plan) == [2, 2, 2]
    lunches = [item for day in plan.days for item in day.items if item.meal_slot == "lunch"]
    dinners = [item for day in plan.days for item in day.items if item.meal_slot == "dinner"]
    assert lunches and all(item.cuisine == "vegetarian" for item in lunches)
    assert all(item.cuisine == "noodles" for item in dinners)
    assert "budget stays as it is" in summary
    assert "Nothing in this plan has a price" not in summary


def test_the_rules_do_not_treat_a_protected_budget_as_a_reprice() -> None:
    assert "budget" not in affected_parts_for(COMPOUND)
    assert "budget" in affected_parts_for("make it cheaper")
    context = TripContext(session_id="s", destination="Kyoto")
    ruled = RuleRouter().route(message=COMPOUND, context=context, plan_summary={"destination": "Kyoto"}, history=[])
    assert rules_decide_alone(COMPOUND, ruled, context=context, plan_summary={"destination": "Kyoto"}) is False


def test_a_question_is_answered_and_does_not_write() -> None:
    shape = interpret_plan(QUESTION)
    plan = _plan()
    before = _counts(plan)

    assert _is_question(QUESTION)
    text = answer_plan_question(plan, shape, budget_question=False)

    assert _counts(plan) == before == [3, 3, 2]
    assert "Day 2 would have at most 1 place" in text
    assert "other days would stay" in text


def test_a_budget_question_with_no_fare_cannot_be_judged() -> None:
    message = "Would a vegetarian lunch stay inside the budget?"
    shape = interpret_plan(message)
    text = answer_plan_question(_plan(), shape, budget_question=True)

    assert "cannot be judged yet" in text


def test_a_missing_hotel_rate_and_flight_fare_are_not_within_budget(settings) -> None:
    plan = _plan()
    context = TripContext(
        session_id="verify",
        destination="Kyoto",
        origin="Hong Kong",
        start_date=date(2026, 11, 1),
        end_date=date(2026, 11, 3),
        budget=200000,
        currency="JPY",
    )
    cost = Planner(None, settings, FxService())._cost(context, plan.first_stay, plan.tickets, plan.dates)  # type: ignore[arg-type]
    check = next(row for row in _constraints(context, plan.days, plan.first_stay, plan.tickets, cost) if row.text.startswith("Stay within"))

    assert cost.total == 0 and cost.status == "unknown"
    assert check.status is ConstraintStatus.UNKNOWN
    assert "cannot be judged yet" in check.evidence


def test_a_question_through_the_edit_does_not_write_the_plan(settings) -> None:
    from tests.test_budget_honesty import _Writer, _edit

    _, updated, _ = _edit(
        settings,
        QUESTION,
        writer=_Writer(is_question=True, affected_parts=["attraction"]),
        route_parts=["attraction"],
    )

    assert "Day 2 would have at most 1 place" in (updated.change_summary or "")
    assert "Nothing in this plan has a price" not in (updated.change_summary or "")


def test_keeping_the_budget_does_not_ask_about_a_missing_price(settings) -> None:
    from tests.test_budget_honesty import _Writer, _edit

    def strip(plan: TripPlan) -> None:
        plan.cost.lines.clear()
        plan.cost.total = 0
        for hotel in plan.hotels:
            hotel.price_per_night = None
        for ticket in plan.tickets:
            ticket.price = None
            ticket.estimated = False

    _, updated, _ = _edit(
        settings,
        COMPOUND,
        writer=_Writer(affected_parts=["attraction", "budget"]),
        route_parts=["attraction", "budget"],
        plan_hook=strip,
    )

    assert "Nothing in this plan has a price" not in (updated.change_summary or "")
    assert "budget stays as it is" in (updated.change_summary or "").lower()


def _is_question(message: str) -> bool:
    from app.application.modification import _is_plan_question

    return _is_plan_question(message)
