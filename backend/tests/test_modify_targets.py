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
    _replace_target,
    _resolve_target,
)
from app.application.planner import Planner
from app.application.planning import PlanPath
from app.application.prompts import PromptLibrary
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.models import ItemType, TripContext
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

    by_fuzzy = _resolve_target(plan, draft, f"{first.name.split()[1]} is too far")
    assert by_fuzzy is not None

    assert _resolve_target(plan, draft, "somewhere entirely different please") is None


def test_meal_is_replaced_with_the_closest_candidate(settings: Settings) -> None:
    _, _, _, _, plan = _build(settings)
    day = next(d for d in plan.days if d.items)
    meal = day.items[0]
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
    day = next(d for d in plan.days if d.items)
    meal = day.items[0]

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
