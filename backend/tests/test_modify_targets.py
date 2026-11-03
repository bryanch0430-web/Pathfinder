"""Regression: naming a stop in a modify message must actually replace it.

Before this, "3. Ho Lee Fook too far away, i want others" resolved to no target
(only quoted names and "swap X for Y" resolved), meals were never eligible for
replacement at all, and the turn published a new version with no change in it.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from app.application.llm_gateway import LLMGateway
from app.application.modification import (
    ChangeDraft,
    ModifyPath,
    _hotel_near,
    _replace_target,
    _resolve_target,
    apply_plan_shape,
    interpret_plan,
    keep_only,
    lock_scope,
    resolve_referred,
)
from app.application.planner import Planner
from app.application.planning import PlanPath
from app.application.prompts import PromptLibrary
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.models import AgentName, ItemType, TripContext
from app.evaluation import SandboxRegistry
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers.fx import FxService


def _build(settings: Settings):
    settings = settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})
    registry = SandboxRegistry(settings, "Kyoto")
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    planner = Planner(gateway, settings, FxService())
    plan_path = PlanPath(planner=planner, registry=registry, settings=settings)
    start = date.today() + timedelta(days=30)
    context = TripContext(
        session_id="s",
        destination="Kyoto",
        origin="Hong Kong",
        start_date=start,
        end_date=start + timedelta(days=2),
        days=3,
        party_size=2,
        budget=400000,
        currency="JPY",
    )
    plan = asyncio.run(
        plan_path.run(
            context=context,
            session_id="s",
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="t"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )
    return gateway, planner, plan_path, context, plan


def test_resolve_target_accepts_index_name_and_rejects_nonsense(settings: Settings) -> None:
    _, _, _, _, plan = _build(settings)
    draft = ChangeDraft(description="", affected_parts=[])
    first = plan.days[0].items[0]

    by_index = _resolve_target(plan, draft, "1. whatever too far away")
    assert by_index is not None and by_index[1].id == first.id

    by_name = _resolve_target(plan, draft, f"I want {first.name} changed")
    assert by_name is not None and by_name[1].id == first.id

    named = next(item for _, item in ((d, i) for d in plan.days for i in d.items) if len(item.name.split()) > 1)
    by_fuzzy = _resolve_target(plan, draft, f"{named.name.split()[1]} is too far")
    assert by_fuzzy is not None

    assert _resolve_target(plan, draft, "somewhere entirely different please") is None


def test_meal_is_replaced_with_the_closest_candidate(settings: Settings) -> None:
    _, _, _, _, plan = _build(settings)
    day, meal = next((d, i) for d in plan.days for i in d.items if i.type is ItemType.MEAL)
    assert meal.type is ItemType.MEAL

    anchor = meal.geo
    nearby = {
        "name": "Nearby Cafe",
        "geo": {"lat": anchor.lat + 0.001, "lng": anchor.lng + 0.001},
        "description": "A short walk away.",
        "source_refs": [],
    }
    far = {
        "name": "Far Cafe",
        "geo": {"lat": anchor.lat + 0.9, "lng": anchor.lng + 0.9},
        "description": "Across the city.",
        "source_refs": [],
    }

    replaced = _replace_target(plan, [], [far, nearby], (day, meal), "too far away", distance_aware=True)

    assert replaced is True
    assert meal.name == "Nearby Cafe", "with a distance complaint the closest candidate must win"
    assert meal.type is ItemType.MEAL, "the slot must stay a meal"


def test_meal_replacement_keeps_first_candidate_without_distance_hint(settings: Settings) -> None:
    _, _, _, _, plan = _build(settings)
    day, meal = next((d, i) for d in plan.days for i in d.items if i.type is ItemType.MEAL)

    replaced = _replace_target(
        plan,
        [],
        [{"name": "Any Cafe", "geo": None, "description": "", "source_refs": []}],
        (day, meal),
        "i want others",
        distance_aware=False,
    )

    assert replaced is True
    assert meal.name == "Any Cafe"


def test_a_selected_meal_is_not_turned_into_a_hotel_change(settings: Settings) -> None:
    _, _, _, _, plan = _build(settings)
    meal = next(item for day in plan.days for item in day.items if item.type is ItemType.MEAL)
    names = lock_scope(
        {AgentName.HOTEL},
        plan,
        "switch this i don't want to eat here",
        meal.id,
    )
    assert AgentName.HOTEL not in names
    assert AgentName.ATTRACTION in names


def test_one_place_a_day_and_no_meals_reshapes_the_plan(settings: Settings) -> None:
    sentence = "ok i want a version of trip plan where, each day, i only visit one place with no meals required"
    shape = interpret_plan(sentence)
    assert shape.requested is True
    assert shape.places_per_day == 1
    assert shape.meals == "drop"
    assert shape.reason == ""
    _, _, _, _, plan = _build(settings)
    hotel = plan.hotels[0].name if plan.hotels else ""
    summary = apply_plan_shape(plan, shape)
    for day in plan.days:
        assert sum(item.type is ItemType.ATTRACTION for item in day.items) <= 1
        assert not any(item.type is ItemType.MEAL for item in day.items)
    if hotel:
        assert plan.hotels[0].name == hotel
    assert "at most 1 place" in summary
    assert "Hotel and flights are unchanged" in summary


def test_a_late_start_and_a_long_visit_are_a_schedule_not_a_hotel(settings: Settings) -> None:
    sentence = (
        "all start after 1400 ok as can't wake up at the morning, "
        "and i want to spend at least 3 hours at a single spot"
    )
    shape = interpret_plan(sentence)
    assert shape.requested is True
    assert shape.earliest == "14:00"
    assert shape.min_hours == 3
    assert shape.reason == ""
    _, _, _, _, plan = _build(settings)
    hotel = plan.hotels[0].name if plan.hotels else ""
    summary = apply_plan_shape(plan, shape)
    for day in plan.days:
        for item in day.items:
            if item.type is ItemType.ATTRACTION:
                assert item.start >= "14:00"
                start_h, start_m = item.start.split(":")
                end_h, end_m = item.end.split(":")
                assert (int(end_h) * 60 + int(end_m)) - (int(start_h) * 60 + int(start_m)) >= 180
    if hotel:
        assert plan.hotels[0].name == hotel
    assert "Hotel and flights are unchanged" in summary
    assert interpret_plan("start after 0200 and spend 3 hours").reason
    assert interpret_plan("spend 12 hours at one spot").reason


def test_an_extreme_pace_is_refused() -> None:
    wiped = interpret_plan("delete the trip and leave no places")
    assert wiped.requested is True and wiped.reason
    packed = interpret_plan("I want 12 sights every day")
    assert packed.requested is True and packed.places_per_day is None and packed.reason
    milder = interpret_plan("can we do two stops a day")
    assert milder.places_per_day == 2 and milder.reason == ""


def test_a_cuisine_request_does_not_swap_in_an_unrelated_restaurant(settings: Settings) -> None:
    _, _, _, _, plan = _build(settings)
    day = next(entry for entry in plan.days if entry.items)
    meal = next(item for item in day.items if item.type is ItemType.MEAL)
    original = meal.name
    replaced = _replace_target(
        plan,
        [],
        [{"name": "Café Bohème TKO", "description": "A cafe in Tseung Kwan O.", "geo": None, "source_refs": []}],
        (day, meal),
        "i want to eat Japanese food",
        distance_aware=False,
        cuisine="japanese",
    )
    assert replaced is False
    assert meal.name == original


def test_a_referred_meal_is_the_only_row_that_may_change(settings: Settings) -> None:
    _, _, _, _, plan = _build(settings)
    meal = next(item for day in plan.days for item in day.items if item.type is ItemType.MEAL)
    assert resolve_referred(plan, "switch this i don't want to eat here", meal.id) == meal.id
    hotel_name = plan.hotels[0].name
    updated = plan.model_copy(deep=True)
    updated.hotels[0].name = "Clothing-Optional Yurts, California"
    for day in updated.days:
        for item in day.items:
            if item.id == meal.id:
                item.name = "Another Restaurant"
    keep_only(plan, updated, meal.id)
    assert updated.hotels[0].name == hotel_name
    assert any(item.name == "Another Restaurant" for day in updated.days for item in day.items)
    assert sum(item.name == "Another Restaurant" for day in updated.days for item in day.items) == 1


def test_a_hotel_on_another_continent_is_rejected(settings: Settings) -> None:
    _, _, _, _, plan = _build(settings)
    assert _hotel_near(plan, {"geo": {"lat": 37.0, "lng": -122.0}}) is False
    here = plan.destination.geo
    assert here is not None
    assert _hotel_near(plan, {"geo": {"lat": here.lat, "lng": here.lng}}) is True


def test_locked_and_confirmed_items_are_never_replaced(settings: Settings) -> None:
    _, _, _, _, plan = _build(settings)
    day = next(d for d in plan.days if d.items)
    meal = day.items[0]
    meal.confirmed = True

    replaced = _replace_target(
        plan,
        [],
        [{"name": "Any Cafe", "geo": None, "description": "", "source_refs": []}],
        (day, meal),
        "too far away",
        distance_aware=True,
    )
    assert replaced is False
    assert meal.name != "Any Cafe"


def test_unmatched_request_does_not_invent_a_version(settings: Settings) -> None:
    gateway, planner, plan_path, context, plan = _build(settings)
    modify = ModifyPath(planner=planner, plan_path=plan_path, settings=settings, gateway=gateway)

    updated, diff = asyncio.run(
        modify.run(
            plan=plan,
            context=context,
            message="Swap the nonexistent Zzyzx Tower for something else.",
            route_parts=["attraction"],
            toolbox=Toolbox(path="modify", tracer=NoopTracer(), store=None, request_id="t"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )

    assert diff == []
    assert updated.version == plan.version, "no change must not be published as a new version"
    assert updated.change_summary != "No itinerary items needed to change."


def test_requested_alternative_takes_the_last_marker() -> None:
    from app.application.modification import _requested_alternative_text

    assert (
        _requested_alternative_text("i want to change oceanpark hong kong the summit to disneyland")
        == "disneyland"
    )
    assert _requested_alternative_text("swap the hotel for something quieter") is None
    assert _requested_alternative_text("make it cheaper") is None


def test_requested_alternative_finds_an_existing_stop(settings: Settings) -> None:
    from app.application.modification import _requested_alternative

    _, _, _, _, plan = _build(settings)
    names = [item.name for day in plan.days for item in day.items]
    assert len(names) >= 2

    found = _requested_alternative(plan, f"change {names[0]} to {names[1]}")

    assert found is not None
    assert found[1].name == names[1]


def test_squashed_name_matching_resolves_joined_words(settings: Settings) -> None:
    _, _, _, _, plan = _build(settings)
    draft = ChangeDraft(description="", affected_parts=[])
    first = plan.days[0].items[0]
    squashed = first.name.replace(" ", "")

    resolved = _resolve_target(plan, draft, f"i want to change {squashed} please")

    assert resolved is not None and resolved[1].id == first.id


def _modify(settings: Settings, message: str, route_parts: list[str], *, new_range: tuple[date, date] | None = None):
    """Build a 3-day Kyoto plan, optionally move the context to `new_range`, and apply one edit."""
    from app.application.modification import ModifyPath
    from app.application.toolbox import Toolbox

    gateway, planner, plan_path, context, plan = _build(settings)
    if new_range is not None:
        context.start_date, context.end_date = new_range
        context.days = (new_range[1] - new_range[0]).days + 1
    modify = ModifyPath(planner=planner, plan_path=plan_path, settings=settings, gateway=gateway)
    updated, diff = asyncio.run(
        modify.run(
            plan=plan,
            context=context,
            message=message,
            route_parts=route_parts,
            toolbox=Toolbox(path="modify", tracer=NoopTracer(), store=None, request_id="t"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )
    return plan, updated, diff


def _next_december(day: int) -> date:
    today = date.today()
    year = today.year if today < date(today.year, 12, day) else today.year + 1
    return date(year, 12, day)


def test_date_change_keeps_the_current_hotel(settings: Settings) -> None:
    from app.application.orchestrator import _describe_diff

    new_range = (_next_december(12), _next_december(14))
    plan, updated, diff = _modify(settings, "move the trip to 12 to 14 December", ["dates"], new_range=new_range)

    assert (updated.dates.start, updated.dates.end) == new_range
    # The hotel the traveller accepted stays; only its dates (and so its price) move.
    assert [hotel.name for hotel in updated.hotels] == [hotel.name for hotel in plan.hotels]
    assert (updated.hotels[0].check_in, updated.hotels[0].check_out) == new_range
    assert not any(change["kind"] == "hotel" for change in diff)
    assert "Hotel changed" not in _describe_diff(diff, updated)
