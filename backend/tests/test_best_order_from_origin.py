"""The best order of the cities starts from where the trip starts, and a planning message can ask for it."""

from __future__ import annotations

import asyncio
from typing import Any, Self

import pytest
from multi_city_rig import CityGeocoder, multi_city_rig, route_context
from plan_samples import make_route_plan
from test_route_edit_turns import MODIFY_ROUTE, PLAN_ROUTE, Trip

from app.application.route_edit import (
    RouteChange,
    apply_route_change,
    rule_route_change,
)
from app.application.route_plan import asks_for_best_order
from app.config import Settings
from app.domain.models import Destination, GeoPoint
from app.eval_metering import RunLedger

KYOTO = GeoPoint(lat=35.0116, lng=135.7681)
OSAKA = GeoPoint(lat=34.6937, lng=135.5023)
TOKYO = GeoPoint(lat=35.6762, lng=139.6503)
HONG_KONG = Destination(name="Hong Kong", geo=GeoPoint(lat=22.3193, lng=114.1694))
BEST_ORDER = "Plan this trip in the best order"


def _plan():
    plan = make_route_plan(["Kyoto", "Tokyo", "Osaka"])
    for segment, geo in zip(plan.segments, [KYOTO, TOKYO, OSAKA]):
        segment.destination.geo = geo
    return plan


def _cities(plan) -> list[str]:
    return [segment.destination.name for segment in plan.segments]


def test_the_best_order_from_the_origin_starts_at_the_city_nearest_it() -> None:
    from_the_first, _ = apply_route_change(_plan(), RouteChange(op="reorder"))
    from_home, sentence = apply_route_change(
        _plan(), RouteChange(op="reorder"), HONG_KONG
    )

    assert [stop.city for stop in from_the_first] == ["Kyoto", "Osaka", "Tokyo"]
    assert [stop.city for stop in from_home] == ["Osaka", "Kyoto", "Tokyo"]
    assert "Hong Kong" in sentence and "Osaka → Kyoto → Tokyo" in sentence


def test_an_origin_without_coordinates_starts_at_the_first_city_as_before() -> None:
    unknown = Destination(name="Nowhere")

    for origin in (None, unknown):
        stops, sentence = apply_route_change(_plan(), RouteChange(op="reorder"), origin)
        assert [stop.city for stop in stops] == ["Kyoto", "Osaka", "Tokyo"]
        assert "Reordered the cities: Kyoto → Osaka → Tokyo." in sentence


def test_the_nights_stay_with_their_city_when_the_order_changes() -> None:
    plan = _plan()
    plan.segments[2].dates = plan.segments[2].dates.model_copy(
        update={
            "end": plan.segments[2].dates.start.replace(
                day=plan.segments[2].dates.start.day + 5
            )
        }
    )

    stops, _ = apply_route_change(plan, RouteChange(op="reorder"), HONG_KONG)

    assert [(stop.city, stop.nights) for stop in stops] == [
        ("Osaka", 5),
        ("Kyoto", 2),
        ("Tokyo", 2),
    ]


@pytest.mark.parametrize(
    ("message", "asked"),
    [
        (
            "Plan Kyoto 2 nights, Tokyo 2 nights and Osaka 2 nights in the best order",
            True,
        ),
        ("plan it in the shortest route please", True),
        ("Plan Kyoto 2 nights, Tokyo 2 nights and Osaka 2 nights", False),
        ("a good order of the days", False),
    ],
)
def test_a_message_asks_for_the_best_order_in_plain_words(
    message: str, asked: bool
) -> None:
    assert asks_for_best_order(message) is asked


def test_the_chat_rule_for_the_best_order_still_reads_the_same_words() -> None:
    plan = _plan()

    assert rule_route_change(
        "what is the best order for the cities", plan
    ) == RouteChange(op="reorder")
    assert rule_route_change("plan the days", plan) is None


class UnlocatedGeocoder(CityGeocoder):
    """The city geocoder, except that it cannot find the places in ``unknown``."""

    def __init__(self, unknown: set[str]) -> None:
        self.unknown = unknown

    async def geocode(self, name: str, *, country_hint: str = ""):
        if name in self.unknown:
            raise RuntimeError("no such place")
        return await super().geocode(name, country_hint=country_hint)


class OriginTrip(Trip):
    """The stored trip of ``Trip``, with the origin the geocoder cannot find when ``unknown`` is given; the first turn
    is ``first`` (the message that plans the trip)."""

    def __init__(
        self,
        settings: Settings,
        stops,
        *,
        first: str = "Plan this trip",
        unknown: set[str] | None = None,
        **more: Any,
    ) -> None:
        super().__init__(settings, stops, **more)
        self.first, self.unknown = first, unknown or set()

    async def __aenter__(self) -> Self:
        self.ledger = RunLedger()
        self.rig = multi_city_rig(self.settings, self.ledger)
        self.rig.plan_path.registry.geocoder = UnlocatedGeocoder(self.unknown)
        self.session = self.ledger.store.create_session("guest-best-order")
        self.ledger.store.save_context(
            route_context(self.session.id, self.stops, **self.more)
        )
        await self.turn(self.first, PLAN_ROUTE)
        return self


def _run(settings: Settings, stops, body, **more: Any):
    async def main():
        async with OriginTrip(settings, stops, **more) as trip:
            return await body(trip)

    return asyncio.run(main())


STOPS = [("Kyoto", 2), ("Tokyo", 2), ("Osaka", 2)]


def test_a_best_order_edit_starts_from_the_origin(settings: Settings) -> None:
    async def body(trip: OriginTrip):
        before = _cities(trip.plan)
        await trip.turn("what is the best order for the cities", MODIFY_ROUTE)
        return before, trip.plan

    before, after = _run(settings, STOPS, body, origin="Hong Kong")

    assert before == ["Kyoto", "Tokyo", "Osaka"]
    assert _cities(after) == ["Osaka", "Kyoto", "Tokyo"]
    assert [segment.dates.night_count for segment in after.segments] == [2, 2, 2]


def test_a_best_order_edit_with_an_origin_that_cannot_be_found_starts_from_the_first_city(
    settings: Settings,
) -> None:
    async def body(trip: OriginTrip):
        await trip.turn("what is the best order for the cities", MODIFY_ROUTE)
        return trip.plan

    after = _run(settings, STOPS, body, origin="Nowhere", unknown={"Nowhere"})

    assert _cities(after) == ["Kyoto", "Osaka", "Tokyo"]
