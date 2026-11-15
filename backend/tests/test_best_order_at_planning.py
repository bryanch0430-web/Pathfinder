"""A planning message that asks for the best order of its cities gets its route planned in that order."""

from __future__ import annotations

from test_best_order_from_origin import BEST_ORDER, STOPS, OriginTrip, _cities, _run

from app.config import Settings


def test_a_planning_message_that_asks_for_the_best_order_plans_the_cities_in_it(
    settings: Settings,
) -> None:
    async def body(trip: OriginTrip):
        return trip.plan, trip.context

    plan, context = _run(settings, STOPS, body, first=BEST_ORDER, origin="Hong Kong")

    assert _cities(plan) == ["Osaka", "Kyoto", "Tokyo"]
    assert [segment.dates.night_count for segment in plan.segments] == [2, 2, 2]
    assert plan.summary.count("best order") == 1
    assert "Hong Kong" in plan.summary and "Osaka → Kyoto → Tokyo" in plan.summary
    assert [stop.city for stop in context.stops] == ["Osaka", "Kyoto", "Tokyo"]


def test_a_planning_message_that_does_not_ask_leaves_the_order_as_given(
    settings: Settings,
) -> None:
    async def body(trip: OriginTrip):
        return trip.plan

    plan = _run(settings, STOPS, body, origin="Hong Kong")

    assert _cities(plan) == ["Kyoto", "Tokyo", "Osaka"]
    assert "best order" not in plan.summary


def test_the_best_order_at_planning_time_with_an_unknown_origin_starts_from_the_first_city(
    settings: Settings,
) -> None:
    async def body(trip: OriginTrip):
        return trip.plan

    plan = _run(
        settings, STOPS, body, first=BEST_ORDER, origin="Nowhere", unknown={"Nowhere"}
    )

    assert _cities(plan) == ["Kyoto", "Osaka", "Tokyo"]
    assert plan.summary.count("best order") == 1 and "Kyoto" in plan.summary


def test_a_city_visited_twice_keeps_its_places_in_the_route_when_the_best_order_is_asked(
    settings: Settings,
) -> None:
    async def body(trip: OriginTrip):
        return trip.plan

    plan = _run(
        settings,
        [("Kyoto", 2), ("Tokyo", 2), ("Kyoto", 2)],
        body,
        first=BEST_ORDER,
        origin="Hong Kong",
    )

    assert _cities(plan) == ["Kyoto", "Tokyo", "Kyoto"]
