"""A lookup the per-turn cap refused is worded as the limit in an edit's reply and in the refresh of an outdated plan.

RX-F6: with the cap at 1, "Add a museum" answered "I could not find a verified museum from the configured
sources" and the refresh of an outdated flight section noted "The source no longer quotes 1 of these flights": both
read as a source's answer, but the lookup was never made. The planning path and the hotel swap already say that the
request reached its limit of data lookups.

Offline: sandbox providers, like the other turn tests.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from app.application.freshness import FreshnessService
from app.application.llm_gateway import LLMGateway
from app.application.modification import ModifyPath
from app.application.planner import Planner
from app.application.router import affected_parts_for
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.errors import TOOL_LIMIT_MESSAGE
from app.domain.models import FailureKind
from app.eval_efficiency import cost_sandbox
from app.infrastructure.observability import NoopTracer
from tests.test_failed_sections import _planned
from tests.test_modify_targets import _build

#: Wording that presents a source's answer.
SOURCE_ANSWER = ("could not find", "came back", "no longer quotes", "no longer lists", "no new forecast", "returned")


async def _spent_lookup(**_: object) -> list:
    return []


def _edit(settings: Settings, message: str, *, cap: int | None):
    """One edit on a Kyoto trip; with a cap of 1 the one call is spent before the edit looks anything up."""
    gateway, planner, plan_path, context, plan = _build(settings)
    toolbox = Toolbox(path="modify", tracer=NoopTracer(), store=None, request_id="t", max_calls=cap)
    if cap is not None:
        asyncio.run(toolbox.call("fx.rates", _spent_lookup))
    updated, _ = asyncio.run(
        ModifyPath(planner=planner, plan_path=plan_path, settings=settings, gateway=gateway).run(
            plan=plan,
            context=context,
            message=message.format(stop=plan.days[0].items[0].name),
            route_parts=affected_parts_for(message),
            toolbox=toolbox,
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )
    return plan, updated, toolbox


def _worded_as_a_source_answer(text: str) -> bool:
    return any(phrase in text.lower() for phrase in SOURCE_ANSWER)


def test_adding_a_stop_whose_search_the_cap_refused_says_the_limit_and_not_that_none_was_found(
    settings: Settings,
) -> None:
    plan, updated, toolbox = _edit(settings, "Add a museum", cap=1)

    assert "places.search" in toolbox.refused
    assert updated.change_summary == TOOL_LIMIT_MESSAGE
    assert not _worded_as_a_source_answer(updated.change_summary)
    kinds = {gap.kind for gap in updated.data_quality.gaps if gap.section == "attractions"}
    assert kinds == {FailureKind.BLOCKED}, "no gap states a source's answer"
    assert updated.version == plan.version


def test_replacing_a_stop_whose_search_the_cap_refused_says_the_limit(settings: Settings) -> None:
    plan, updated, toolbox = _edit(settings, "replace {stop} with a temple", cap=1)

    assert "places.search" in toolbox.refused
    assert updated.change_summary == TOOL_LIMIT_MESSAGE
    assert updated.version == plan.version


def test_replacing_a_stop_with_a_cuisine_whose_search_the_cap_refused_says_the_limit(settings: Settings) -> None:
    _, updated, toolbox = _edit(settings, "replace {stop} with a sushi restaurant", cap=1)

    assert "places.search" in toolbox.refused
    assert updated.change_summary == TOOL_LIMIT_MESSAGE


def test_an_add_that_ran_its_search_and_found_nothing_still_says_none_was_found(settings: Settings) -> None:
    gateway, planner, plan_path, context, plan = _build(settings)
    plan_path.registry.places = []  # the source has nothing to offer
    toolbox = Toolbox(path="modify", tracer=NoopTracer(), store=None, request_id="t")

    updated, _ = asyncio.run(
        ModifyPath(planner=planner, plan_path=plan_path, settings=settings, gateway=gateway).run(
            plan=plan,
            context=context,
            message="Add a museum",
            route_parts=affected_parts_for("Add a museum"),
            toolbox=toolbox,
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )

    assert not toolbox.refused
    assert "could not find a verified museum" in updated.change_summary


def _stale_scene(settings: Settings, gateway: LLMGateway, cap: int | None):
    """A Kyoto plan looked at when every section is older than its window, by a service whose turns are capped."""
    registry = cost_sandbox(settings, "Kyoto")
    planner = Planner(gateway, settings, registry.fx)
    plan = asyncio.run(_planned(registry, planner, settings))
    capped = settings.model_copy(update={"max_tool_calls_per_turn": cap or 0})
    now = plan.updated_at + timedelta(seconds=max(settings.hotel_fresh_seconds, settings.ticket_fresh_seconds) + 86_400)
    service = FreshnessService(registry=registry, planner=planner, settings=capped, clock=lambda: now)
    return plan, service


def test_the_refresh_of_an_outdated_plan_says_the_limit_for_a_section_whose_lookup_the_cap_refused(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    plan, service = _stale_scene(settings, offline_gateway, cap=1)

    shown = asyncio.run(service.on_reopen(plan))

    notes = {mark.section: mark.note for mark in shown.data_quality.freshness if mark.status == "outdated"}
    assert notes, "with one lookup for three sections, two stay outdated"
    assert set(notes.values()) == {TOOL_LIMIT_MESSAGE}, notes
    assert not any(_worded_as_a_source_answer(note) for note in notes.values())


def test_the_refresh_of_an_outdated_plan_with_no_cap_is_unchanged(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    plan, service = _stale_scene(settings, offline_gateway, cap=None)

    shown = asyncio.run(service.on_reopen(plan))

    assert all(mark.status == "refreshed" for mark in shown.data_quality.freshness)
    assert not any(mark.note == TOOL_LIMIT_MESSAGE for mark in shown.data_quality.freshness)


def test_the_note_of_a_section_the_source_really_answered_is_still_the_sources(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    registry = cost_sandbox(settings, "Kyoto")
    planner = Planner(offline_gateway, settings, registry.fx)
    plan = asyncio.run(_planned(registry, planner, settings))

    class Nothing:
        name = "nothing-hotels"
        available = True

        async def search(self, **_: object) -> list:
            return []

    registry.hotels = [Nothing()]
    now = plan.updated_at + timedelta(seconds=settings.hotel_fresh_seconds + 86_400)
    service = FreshnessService(registry=registry, planner=planner, settings=settings, clock=lambda: now)

    shown = asyncio.run(service.on_reopen(plan))

    [mark] = [mark for mark in shown.data_quality.freshness if mark.section == "hotels"]
    assert mark.status == "outdated" and "no longer lists a price" in mark.note
