"""A forecast, fare or stay that failed when the plan was made is fetched again.

The freshness check used to look only at the fetch times of data that exists, so a section whose
source was down at planning had no time to be outdated and stayed empty for good. Such a section (the
plan holds none of it and names a retryable gap for it) is asked for again when the plan is reopened or
the traveller asks to refresh, and while its source stays down it is not asked on every reopen.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient

from app.application.freshness import RETRY_AFTER_SECONDS, SECTIONS, FreshnessService
from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner
from app.application.planning import PlanPath
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.errors import ProviderError
from app.domain.models import EvalCase, FailureKind, TripPlan, utcnow
from app.eval_efficiency import accepted_plan, build_rig, case_context, cost_sandbox
from app.eval_metering import RunLedger
from app.infrastructure.observability import NoopTracer
from app.interface.api import create_app


class Switch:
    """A source that times out while it is ``down`` and otherwise answers as the sandbox does."""

    available = True

    def __init__(self, real: Any, method: str) -> None:
        self.name = real.name
        self._real = real
        self._method = method
        self.down = True
        self.asked = 0
        setattr(self, method, self._answer)

    async def _answer(self, **kwargs: Any) -> Any:
        self.asked += 1
        if self.down:
            raise ProviderError(f"{self.name} timed out", kind=FailureKind.TIMEOUT, source=self.name, retryable=True)
        return await getattr(self._real, self._method)(**kwargs)


def _switches(registry: Any, *, down: tuple[str, ...] = SECTIONS) -> dict[str, Switch]:
    registry.weather = [Switch(registry.weather[0], "forecast")]
    registry.hotels = [Switch(registry.hotels[0], "search")]
    registry.flights = [Switch(registry.flights[0], "search")]
    sources = {"weather": registry.weather[0], "hotels": registry.hotels[0], "tickets": registry.flights[0]}
    for name, source in sources.items():
        source.down = name in down
    return sources


def _context(session_id: str = "s"):
    return case_context(EvalCase(id="f", dataset="design", input="", destination="Kyoto", days=3), session_id)


async def _planned(registry: Any, planner: Planner, settings: Settings, session_id: str = "s") -> TripPlan:
    path = PlanPath(planner=planner, registry=registry, settings=settings)
    return await path.run(
        context=_context(session_id),
        session_id=session_id,
        toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="req_plan"),
        tracer=NoopTracer(),
        emit=lambda *_: None,
    )


@dataclass
class Scene:
    sources: dict[str, Switch]
    plan: TripPlan
    service: FreshnessService
    clock: list[datetime]

    def reopen(self, plan: TripPlan | None = None) -> TripPlan:
        return asyncio.run(self.service.on_reopen(plan or self.plan))

    def asked(self) -> dict[str, int]:
        return {name: source.asked for name, source in self.sources.items()}


def _scene(settings: Settings, gateway: LLMGateway, *, down: tuple[str, ...] = SECTIONS) -> Scene:
    """A Kyoto plan made while the sources named in ``down`` were failing."""
    registry = cost_sandbox(settings, "Kyoto")
    sources = _switches(registry, down=down)
    planner = Planner(gateway, settings, registry.fx)
    plan = asyncio.run(_planned(registry, planner, settings))
    clock = [plan.updated_at]
    service = FreshnessService(registry=registry, planner=planner, settings=settings, clock=lambda: clock[0])
    return Scene(sources=sources, plan=plan, service=service, clock=clock)


def _sections_with_gaps(plan: TripPlan) -> set[str]:
    return {gap.section for gap in plan.data_quality.gaps if gap.section in SECTIONS}


def test_a_plan_made_while_its_sources_were_down_holds_none_of_their_data(settings: Settings, offline_gateway: LLMGateway) -> None:
    """The starting point of the other tests: nothing to date, and a gap that says a new try may work."""
    plan = _scene(settings, offline_gateway).plan

    assert [day.weather for day in plan.days] == [None] * 3
    assert plan.hotels == [] and plan.tickets == []
    retryable = {gap.section for gap in plan.data_quality.gaps if gap.retryable}
    assert set(SECTIONS) <= retryable


def test_a_section_that_failed_when_the_plan_was_made_is_fetched_again_when_the_trip_is_reopened(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    scene = _scene(settings, offline_gateway)
    for source in scene.sources.values():
        source.down = False
    scene.clock[0] = scene.plan.updated_at + timedelta(days=1)

    shown = scene.reopen()

    assert all(day.weather is not None for day in shown.days)
    assert [hotel.name for hotel in shown.hotels] == ["The Kyoto Grand"]
    assert [ticket.name for ticket in shown.tickets] == ["Pathfinder Air PF100"]
    assert _sections_with_gaps(shown) == set()
    assert shown.version == scene.plan.version + 1
    assert shown.change_summary == "Fetched the missing weather forecast, flight fares and hotel prices."


def test_what_is_fetched_afterwards_is_what_a_plan_made_with_the_sources_up_holds(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    """The stay, the fares and the forecast, and what follows from them: the cost, the checks, the bookings."""
    scene = _scene(settings, offline_gateway)
    for source in scene.sources.values():
        source.down = False
    scene.clock[0] = scene.plan.updated_at + timedelta(days=1)
    made_whole = asyncio.run(accepted_plan(settings, _context()))

    shown = scene.reopen()

    assert [day.weather.summary for day in shown.days] == [day.weather.summary for day in made_whole.days]
    assert [(hotel.name, hotel.price_per_night) for hotel in shown.hotels] == [
        (hotel.name, hotel.price_per_night) for hotel in made_whole.hotels
    ]
    assert [(ticket.name, ticket.price) for ticket in shown.tickets] == [
        (ticket.name, ticket.price) for ticket in made_whole.tickets
    ]
    assert shown.cost.model_dump() == made_whole.cost.model_dump()
    assert [(check.text, check.status) for check in shown.constraints] == [
        (check.text, check.status) for check in made_whole.constraints
    ]
    assert [reservation.name for reservation in shown.reservations] == [
        reservation.name for reservation in made_whole.reservations
    ]
    assert set(made_whole.data_quality.sources_used) <= set(shown.data_quality.sources_used)


def test_only_the_section_that_failed_is_fetched_again(settings: Settings, offline_gateway: LLMGateway) -> None:
    scene = _scene(settings, offline_gateway, down=("hotels",))
    before = scene.asked()
    scene.sources["hotels"].down = False
    scene.clock[0] = scene.plan.updated_at + timedelta(hours=1)

    shown = scene.reopen()

    assert {name: scene.asked()[name] - before[name] for name in SECTIONS} == {"weather": 0, "hotels": 1, "tickets": 0}
    assert [hotel.name for hotel in shown.hotels] == ["The Kyoto Grand"]
    assert shown.change_summary == "Fetched the missing hotel prices."


def test_a_source_that_is_still_down_is_asked_again_no_more_often_than_every_ten_minutes(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    scene = _scene(settings, offline_gateway)
    start = scene.asked()
    scene.clock[0] = scene.plan.updated_at + timedelta(days=1)

    first = scene.reopen()
    assert {name: scene.asked()[name] - start[name] for name in SECTIONS} == {name: 1 for name in SECTIONS}
    assert first.version == scene.plan.version  # nothing came back, so there is nothing to save

    scene.clock[0] += timedelta(seconds=RETRY_AFTER_SECONDS - 1)
    scene.reopen()
    assert {name: scene.asked()[name] - start[name] for name in SECTIONS} == {name: 1 for name in SECTIONS}

    scene.clock[0] += timedelta(seconds=2)
    scene.reopen()
    assert {name: scene.asked()[name] - start[name] for name in SECTIONS} == {name: 2 for name in SECTIONS}


def test_two_reopenings_at_once_ask_a_source_that_is_down_only_once(settings: Settings, offline_gateway: LLMGateway) -> None:
    scene = _scene(settings, offline_gateway)
    start = scene.asked()
    scene.clock[0] = scene.plan.updated_at + timedelta(days=1)

    async def both() -> None:
        await asyncio.gather(scene.service.on_reopen(scene.plan), scene.service.on_reopen(scene.plan))

    asyncio.run(both())

    assert {name: scene.asked()[name] - start[name] for name in SECTIONS} == {name: 1 for name in SECTIONS}


def test_a_check_fetches_what_is_outdated_and_what_never_arrived_together(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    scene = _scene(settings, offline_gateway, down=("weather",))
    scene.sources["weather"].down = False
    scene.clock[0] = scene.plan.updated_at + timedelta(seconds=settings.ticket_fresh_seconds + 60)

    shown = scene.reopen()

    assert all(day.weather is not None for day in shown.days)
    assert [(mark.section, mark.status) for mark in shown.data_quality.freshness] == [("tickets", "refreshed")]
    assert shown.change_summary == "Refreshed the outdated flight fares. Fetched the missing weather forecast."


def test_a_plan_just_made_is_not_asked_again_at_once(settings: Settings, offline_gateway: LLMGateway) -> None:
    scene = _scene(settings, offline_gateway)
    start = scene.asked()
    scene.clock[0] = scene.plan.updated_at + timedelta(seconds=30)

    scene.reopen()

    assert scene.asked() == start


def test_a_section_without_a_retryable_gap_is_not_asked_again(settings: Settings, offline_gateway: LLMGateway) -> None:
    """The source said there is nothing for these dates (or the trip has no night): asking again would not help."""
    scene = _scene(settings, offline_gateway)
    for gap in scene.plan.data_quality.gaps:
        gap.retryable = False
    start = scene.asked()
    scene.clock[0] = scene.plan.updated_at + timedelta(days=1)

    scene.reopen()

    assert scene.asked() == start


def test_a_trip_that_has_ended_is_not_asked_again(settings: Settings, offline_gateway: LLMGateway) -> None:
    scene = _scene(settings, offline_gateway)
    start = scene.asked()
    scene.clock[0] = datetime.combine(scene.plan.dates.end + timedelta(days=2), datetime.min.time(), tzinfo=utcnow().tzinfo)

    scene.reopen()

    assert scene.asked() == start


# -- "Refresh the data" in the chat -------------------------------------------------------------


def _chat(rig: Any, session_id: str, message: str) -> tuple[str, list]:
    async def run() -> list:
        return [event async for event in rig.turns.handle_turn(session_id=session_id, message=message)]

    events = asyncio.run(run())
    return "".join(event.data["text"] for event in events if event.event == "token"), events


def _planned_in_chat(settings: Settings, ledger: RunLedger, *, down: tuple[str, ...] = SECTIONS):
    rig = build_rig(settings, "Kyoto", ledger)
    sources = _switches(rig.plan_path.registry, down=down)
    session_id = ledger.store.create_session("guest-failed-sections").id
    ledger.store.save_context(_context(session_id))
    rig.llm.route = {"intent": "plan", "confidence": 0.95}
    _, events = _chat(rig, session_id, "Plan the Kyoto trip now.")
    assert not [event for event in events if event.event == "error"]
    return rig, sources, session_id


def test_refresh_the_data_fetches_what_failed_when_the_plan_was_made(settings: Settings) -> None:
    ledger = RunLedger()
    try:
        rig, sources, session_id = _planned_in_chat(settings, ledger)
        planned = ledger.store.get_plan(session_id)
        for source in sources.values():
            source.down = False

        reply, events = _chat(rig, session_id, "Refresh the data")
        stored = ledger.store.get_plan(session_id)
    finally:
        ledger.close()

    assert reply == "Fetched the missing weather forecast, flight fares and hotel prices."
    assert stored.version == planned.version + 1
    assert all(day.weather is not None for day in stored.days) and stored.hotels and stored.tickets
    assert _sections_with_gaps(stored) == set()
    assert [event.event for event in events].count("plan") == 1


def test_refresh_the_data_asks_again_at_once_whatever_the_time_since_the_last_try(settings: Settings) -> None:
    ledger = RunLedger()
    try:
        rig, sources, session_id = _planned_in_chat(settings, ledger)
        before = {name: source.asked for name, source in sources.items()}

        _chat(rig, session_id, "Refresh the data")
        _chat(rig, session_id, "Refresh the data")
        asked = {name: source.asked - before[name] for name, source in sources.items()}
    finally:
        ledger.close()

    assert asked == {name: 2 for name in SECTIONS}


def test_refresh_the_data_says_what_is_still_missing_when_the_sources_are_still_down(settings: Settings) -> None:
    ledger = RunLedger()
    try:
        rig, _, session_id = _planned_in_chat(settings, ledger)
        planned = ledger.store.get_plan(session_id)

        reply, events = _chat(rig, session_id, "Refresh the data")
        stored = ledger.store.get_plan(session_id)
    finally:
        ledger.close()

    assert "freshness window" not in reply
    assert reply == "Still missing after trying again: weather forecast, flight fares and hotel prices."
    assert stored.version == planned.version
    assert [event.event for event in events].count("plan") == 0


def test_refresh_the_data_names_only_the_section_that_is_still_missing(settings: Settings) -> None:
    ledger = RunLedger()
    try:
        rig, sources, session_id = _planned_in_chat(settings, ledger)
        sources["weather"].down = False
        sources["tickets"].down = False

        reply, _ = _chat(rig, session_id, "Refresh the data")
        stored = ledger.store.get_plan(session_id)
    finally:
        ledger.close()

    assert reply == "Fetched the missing weather forecast and flight fares. Still missing after trying again: hotel prices."
    assert stored.hotels == [] and stored.tickets and _sections_with_gaps(stored) == {"hotels"}


def test_a_question_about_the_trip_fetches_what_never_arrived_before_it_is_answered(settings: Settings) -> None:
    ledger = RunLedger()
    try:
        rig, sources, session_id = _planned_in_chat(settings, ledger)
        planned = ledger.store.get_plan(session_id)
        for source in sources.values():
            source.down = False
        rig.turns.freshness.clock = lambda: planned.updated_at + timedelta(days=1)
        rig.llm.route = {"intent": "ask", "confidence": 0.95}

        _, events = _chat(rig, session_id, "What will the weather be like on day 1?")
        stored = ledger.store.get_plan(session_id)
    finally:
        ledger.close()

    assert stored.version == planned.version + 1
    assert all(day.weather is not None for day in stored.days) and stored.hotels and stored.tickets
    shown = [event.data["plan"] for event in events if event.event == "plan"]
    assert [plan["version"] for plan in shown] == [stored.version]


def test_a_question_about_the_trip_does_not_ask_a_source_that_is_still_down_every_time(settings: Settings) -> None:
    ledger = RunLedger()
    try:
        rig, sources, session_id = _planned_in_chat(settings, ledger)
        planned = ledger.store.get_plan(session_id)
        before = {name: source.asked for name, source in sources.items()}
        rig.turns.freshness.clock = lambda: planned.updated_at + timedelta(days=1)
        rig.llm.route = {"intent": "ask", "confidence": 0.95}

        for _ in range(3):
            _, events = _chat(rig, session_id, "What is the first stop on day 1?")
        asked = {name: source.asked - before[name] for name, source in sources.items()}
        stored = ledger.store.get_plan(session_id)
    finally:
        ledger.close()

    assert asked == {name: 1 for name in SECTIONS}
    assert stored.version == planned.version
    assert not [event for event in events if event.event == "plan"]


def test_refresh_the_data_on_a_complete_plan_still_says_nothing_needed_refreshing(settings: Settings) -> None:
    ledger = RunLedger()
    try:
        rig, _, session_id = _planned_in_chat(settings, ledger, down=())

        reply, _ = _chat(rig, session_id, "Refresh the data")
    finally:
        ledger.close()

    assert reply == "Every section of the plan is within its freshness window; nothing needed refreshing."


def test_refresh_the_data_in_traditional_chinese_names_what_is_still_missing(settings: Settings) -> None:
    ledger = RunLedger()
    try:
        rig, _, session_id = _planned_in_chat(settings, ledger)
        context = ledger.store.get_context(session_id)
        context.plan_locale = "zh-Hant"
        ledger.store.save_context(context)

        reply, _ = _chat(rig, session_id, "Refresh the data")
    finally:
        ledger.close()

    assert reply == "重試後仍然缺少：天氣預報、機票價格、酒店價格。"


# -- reopening a trip over HTTP -----------------------------------------------------------------


def test_reopening_a_trip_over_http_fetches_what_failed_when_it_was_planned(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    registry = cost_sandbox(settings, "Kyoto")
    sources = _switches(registry)
    with TestClient(create_app(settings)) as client:
        container = client.app.state.container
        guest = {"X-Client-Token": "guest-failed-http"}
        session_id = client.post("/api/journeys", json={"client_token": "guest-failed-http"}).json()["session"]["id"]
        freshness = container.turn_service.freshness
        freshness.registry = registry
        container.store.save_context(_context(session_id))
        plan = asyncio.run(_planned(registry, Planner(offline_gateway, settings, registry.fx), settings, session_id))
        container.store.save_plan(plan, "Initial plan")
        for source in sources.values():
            source.down = False
        freshness.clock = lambda: plan.updated_at + timedelta(days=1)

        opened = client.get(f"/api/journeys/{session_id}", headers=guest).json()
        again = client.get(f"/api/journeys/{session_id}/itinerary", headers=guest).json()["plan"]

    shown = opened["plan"]
    assert [day["weather"] is not None for day in shown["days"]] == [True] * 3
    assert [hotel["name"] for hotel in shown["hotels"]] == ["The Kyoto Grand"]
    assert [gap["section"] for gap in shown["data_quality"]["gaps"] if gap["section"] in SECTIONS] == []
    assert shown["version"] == again["version"] == plan.version + 1
    assert sorted(version["version"] for version in opened["versions"]) == [1, 2]
