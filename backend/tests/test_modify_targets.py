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


def _build(settings: Settings, days: int = 3):
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
        end_date=start + timedelta(days=days - 1),
        days=days,
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


def _modify(
    settings: Settings,
    message: str,
    route_parts: list[str],
    *,
    new_range: tuple[date, date] | None = None,
    new_days: int | None = None,
    days: int = 3,
    prepare=None,
    break_registry=None,
):
    """Build a Kyoto plan of `days` days, optionally let `prepare(plan)` edit it and
    `break_registry(registry)` change the sources the edit will use, move the context to
    `new_range` (or to `new_days` days from the start), and apply one edit."""
    from app.application.modification import ModifyPath
    from app.application.toolbox import Toolbox

    gateway, planner, plan_path, context, plan = _build(settings, days=days)
    if prepare is not None:
        prepare(plan)
    if break_registry is not None:
        break_registry(plan_path.registry)
    if new_days is not None:
        new_range = (context.start_date, context.start_date + timedelta(days=new_days - 1))
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


def _where(plan, name: str) -> list[tuple[int, object]]:
    return [(index, item) for index, day in enumerate(plan.days) for item in day.items if item.name == name]


def test_locked_stop_survives_a_length_change(settings: Settings) -> None:
    holder: dict = {}

    def lock_a_day_four_stop(plan) -> None:
        plan.days[3].items[0].locked = True
        holder["name"] = plan.days[3].items[0].name

    _, updated, _ = _modify(
        settings, "make it 4 days", ["dates"], days=5, new_days=4, prepare=lock_a_day_four_stop
    )

    assert len(updated.days) == 4
    found = _where(updated, holder["name"])
    assert len(found) == 1 and found[0][1].locked is True
    # On the same day if that day still exists, otherwise on the last day.
    assert found[0][0] == min(3, len(updated.days) - 1)


def _lock_a_stop_the_rebuild_will_not_plan(plan) -> None:
    extra = plan.days[3].items[0].model_copy(deep=True)
    extra.id, extra.name, extra.start, extra.end, extra.locked = "item_custom", "Philosopher's Path", "14:00", "15:30", True
    plan.days[3].items.append(extra)


def test_locked_stop_missing_from_the_rebuild_is_put_back(settings: Settings) -> None:
    _, updated, _ = _modify(
        settings, "make it 4 days", ["dates"], days=5, new_days=4, prepare=_lock_a_stop_the_rebuild_will_not_plan
    )

    found = _where(updated, "Philosopher's Path")
    assert [(index, item.locked) for index, item in found] == [(3, True)]
    day = updated.days[3]
    assert [item.start for item in day.items] == sorted(item.start for item in day.items)
    assert len({item.id for plan_day in updated.days for item in plan_day.items}) == updated.item_count
    assert any("Kept your locked stop Philosopher's Path on day 4." in gap.message for gap in updated.data_quality.gaps)


def test_locked_stop_on_a_removed_day_moves_to_the_last_day(settings: Settings) -> None:
    _, updated, _ = _modify(
        settings, "make it 3 days", ["dates"], days=5, new_days=3, prepare=_lock_a_stop_the_rebuild_will_not_plan
    )

    assert len(updated.days) == 3
    found = _where(updated, "Philosopher's Path")
    assert [(index, item.locked) for index, item in found] == [(2, True)]
    assert any("Kept your locked stop Philosopher's Path on day 3." in gap.message for gap in updated.data_quality.gaps)


class _TimingOutWeather:
    name = "timing-out-weather"

    async def forecast(self, geo, start, end):
        raise TimeoutError("the forecast source timed out")


def test_date_shift_with_failed_weather_leaves_no_old_forecast(settings: Settings) -> None:
    from app.domain.models import FailureKind

    start = date.today() + timedelta(days=30)
    new_range = (start + timedelta(days=9), start + timedelta(days=11))

    plan, updated, _ = _modify(
        settings,
        "move the trip 9 days later",
        ["dates"],
        new_range=new_range,
        break_registry=lambda registry: setattr(registry, "weather", [_TimingOutWeather()]),
    )

    # The change went through, and no day shows the forecast of another date.
    assert (updated.dates.start, updated.dates.end) == new_range
    assert plan.days[0].weather is not None
    assert all(day.weather is None or day.weather.date == day.date for day in updated.days)
    assert all(day.weather is None or day.weather.date in updated.dates.dates for day in updated.days)
    assert (FailureKind.TIMEOUT, "weather") in [(gap.kind, gap.section) for gap in updated.data_quality.gaps]
