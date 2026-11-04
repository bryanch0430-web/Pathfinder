"""An outdated section that its source cannot renew is not asked again on every look at the trip.

Only a section that never arrived was kept to the ten-minute record. An outdated one stays outdated (its fetch
time does not move when the source has nothing), so every reopening, every question about the trip and the page's
own refresh after each message called the billable source again, and each reopening wrote a trace file. It is now
asked again no more often than every ten minutes, still shown as outdated meanwhile, at once when the traveller
asks to refresh, and a look at the trip that asks nothing writes no trace file.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.application.freshness import RETRY_AFTER_SECONDS, FreshnessService
from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner
from app.config import Settings
from app.domain.models import TripPlan
from app.eval_efficiency import cost_sandbox
from app.infrastructure.observability import NoopTracer
from tests.test_failed_sections import _planned


class Nothing:
    """A hotel source that answers every search with no hotels."""

    name = "nothing-hotels"
    available = True

    def __init__(self) -> None:
        self.asked = 0

    async def search(self, **_: Any) -> list:
        self.asked += 1
        return []


@dataclass
class Scene:
    hotels: Nothing
    plan: TripPlan
    service: FreshnessService
    clock: list[datetime]

    def reopen(self, plan: TripPlan) -> TripPlan:
        return asyncio.run(self.service.on_reopen(plan))


def _scene(settings: Settings, gateway: LLMGateway) -> Scene:
    """A whole Kyoto plan, looked at when its hotel price is a day older than the window, and the hotel source has nothing."""
    registry = cost_sandbox(settings, "Kyoto")
    planner = Planner(gateway, settings, registry.fx)
    plan = asyncio.run(_planned(registry, planner, settings))
    hotels = Nothing()
    registry.hotels = [hotels]
    clock = [plan.updated_at + timedelta(seconds=settings.hotel_fresh_seconds + 86_400)]
    service = FreshnessService(registry=registry, planner=planner, settings=settings, clock=lambda: clock[0])
    return Scene(hotels=hotels, plan=plan, service=service, clock=clock)


def _marks(plan: TripPlan) -> dict[str, str]:
    return {mark.section: mark.status for mark in plan.data_quality.freshness}


def _traces(settings: Settings) -> int:
    return len(list(settings.trace_dir.glob("*.json"))) if settings.trace_dir.exists() else 0


def test_an_outdated_section_its_source_cannot_renew_is_asked_again_no_more_often_than_every_ten_minutes(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    scene = _scene(settings, offline_gateway)

    shown = scene.reopen(scene.plan)
    assert scene.hotels.asked == 1
    assert _marks(shown) == {"weather": "refreshed", "tickets": "refreshed", "hotels": "outdated"}, "the others were renewed"

    for _ in range(4):
        shown = scene.reopen(shown)
    assert scene.hotels.asked == 1

    scene.clock[0] += timedelta(seconds=RETRY_AFTER_SECONDS - 1)
    shown = scene.reopen(shown)
    assert scene.hotels.asked == 1

    scene.clock[0] += timedelta(seconds=2)
    scene.reopen(shown)
    assert scene.hotels.asked == 2


def test_an_outdated_section_that_is_not_asked_again_yet_is_still_shown_as_outdated(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    scene = _scene(settings, offline_gateway)
    shown = scene.reopen(scene.plan)

    again = scene.reopen(shown)

    assert scene.hotels.asked == 1
    [mark] = [mark for mark in again.data_quality.freshness if mark.section == "hotels"]
    assert mark.status == "outdated" and mark.fetched_at == shown.hotels[0].fetched_at
    assert [hotel.model_dump() for hotel in again.hotels] == [hotel.model_dump() for hotel in shown.hotels]


def test_refresh_the_data_asks_an_outdated_section_at_once(settings: Settings, offline_gateway: LLMGateway) -> None:
    scene = _scene(settings, offline_gateway)
    shown = scene.reopen(scene.plan)
    assert scene.hotels.asked == 1

    outcome = asyncio.run(
        scene.service.check(shown, context=None, tracer=NoopTracer(), request_id="req_ask", save=False, requested=True)
    )

    assert scene.hotels.asked == 2
    assert outcome is not None and outcome.outdated == ["hotels"] and outcome.refreshed == []


def test_a_look_at_the_trip_that_asks_nothing_writes_no_trace_file(settings: Settings, offline_gateway: LLMGateway) -> None:
    scene = _scene(settings, offline_gateway)
    shown = scene.reopen(scene.plan)
    written = _traces(settings)
    assert written == 1, "the look that asked the sources is on record"

    for _ in range(3):
        shown = scene.reopen(shown)

    assert _traces(settings) == written


# -- the record is made when the source is asked, not when the answer is in --------------------------------


class Slow(Nothing):
    """A hotel source that takes a moment to say it has nothing."""

    async def search(self, **_: Any) -> list:
        self.asked += 1
        await asyncio.sleep(0.05)
        return []


def test_two_reopenings_at_once_ask_a_source_that_cannot_renew_an_outdated_section_only_once(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    scene = _scene(settings, offline_gateway)
    slow = Slow()
    scene.service.registry.hotels = [slow]

    async def both() -> None:
        await asyncio.gather(scene.service.on_reopen(scene.plan), scene.service.on_reopen(scene.plan))

    asyncio.run(both())

    assert slow.asked == 1


def test_a_look_that_is_cancelled_before_the_source_answered_does_not_keep_the_next_one_from_asking(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    scene = _scene(settings, offline_gateway)
    slow = Slow()
    scene.service.registry.hotels = [slow]

    async def cancelled_then_again() -> None:
        look = asyncio.ensure_future(scene.service.on_reopen(scene.plan))
        await asyncio.sleep(0.01)  # the source has been asked and has not answered
        look.cancel()
        await asyncio.gather(look, return_exceptions=True)
        await scene.service.on_reopen(scene.plan)

    asyncio.run(cancelled_then_again())

    assert slow.asked == 2


def test_what_was_asked_long_ago_is_forgotten_and_what_was_asked_lately_is_not(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    scene = _scene(settings, offline_gateway)
    now = scene.clock[0]
    scene.service._tried[("another-trip", "hotels")] = now - timedelta(seconds=RETRY_AFTER_SECONDS + 60)
    scene.service._tried[("a-trip-asked-lately", "hotels")] = now - timedelta(seconds=60)

    scene.reopen(scene.plan)

    assert ("another-trip", "hotels") not in scene.service._tried
    assert ("a-trip-asked-lately", "hotels") in scene.service._tried
