"""A request for other dates on a trip of several cities: every city moves by the same number of days."""

from __future__ import annotations

from datetime import timedelta

from test_route_edit_turns import Trip, _run

from app.application.multi_edit import RELEASED_LOCK
from app.config import Settings

WEEK = timedelta(days=7)


def _save_form_shifted(trip: Trip, by: timedelta) -> None:
    """What the page's Save does: the trip form takes other dates, the plan keeps its own."""
    context = trip.context
    context.start_date += by
    context.end_date += by
    trip.ledger.store.save_context(context)


def _lock(trip: Trip, city: str, leg: tuple[str, str]) -> None:
    plan = trip.plan
    segment = next(s for s in plan.segments if s.destination.name == city)
    for stay in plan.stays_in(segment.id):
        stay.locked = True
    next(l for l in plan.legs if (l.from_place, l.to_place) == leg).locked = True
    trip.ledger.store.save_plan(plan, None, replace=True)


def test_moving_a_two_city_trip_a_week_later_moves_both_cities_and_keeps_their_nights(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        before = trip.plan
        hotels, flights = (
            trip.searches("hotels.search"),
            trip.searches("flights.search"),
        )
        _save_form_shifted(trip, WEEK)
        events = await trip.turn("move the trip a week later")
        return (
            before,
            trip.plan,
            events,
            trip.searches("hotels.search") - hotels,
            trip.searches("flights.search") - flights,
        )

    before, after, events, hotel_searches, flight_searches = _run(
        settings, [("Kyoto", 2), ("Osaka", 2)], body
    )

    assert not [e for e in events if e.event == "error"]
    assert after.version == before.version + 1
    assert (after.dates.start, after.dates.end) == (
        before.dates.start + WEEK,
        before.dates.end + WEEK,
    )
    assert [
        (s.destination.name, s.dates.start, s.dates.night_count) for s in after.segments
    ] == [
        (s.destination.name, s.dates.start + WEEK, s.dates.night_count)
        for s in before.segments
    ]
    assert [d.date for d in after.days] == [d.date + WEEK for d in before.days]
    # Each city is planned again for its new dates, and every leg is looked up again.
    assert hotel_searches == 2 and flight_searches >= 1
    assert [leg.depart_date for leg in after.route_legs()] == [
        leg.depart_date + WEEK for leg in before.route_legs()
    ]
    for stay in after.hotels:
        segment = next(s for s in after.segments if s.id == stay.segment_id)
        assert (
            segment.dates.start <= stay.check_in < stay.check_out <= segment.dates.end
        )


def test_moving_a_two_city_trip_releases_its_locks_with_the_sentence(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        _lock(trip, "Kyoto", ("Kyoto", "Osaka"))
        _save_form_shifted(trip, WEEK)
        await trip.turn("move the trip a week later")
        return trip.plan

    after = _run(settings, [("Kyoto", 2), ("Osaka", 2)], body)

    assert RELEASED_LOCK.format(what="Kyoto → Osaka") in after.change_summary
    assert RELEASED_LOCK.format(what="the stay in Kyoto") in after.change_summary
    assert not any(leg.locked for leg in after.legs) and not any(
        stay.locked for stay in after.hotels
    )


def test_a_request_for_other_dates_without_new_dates_in_the_form_changes_nothing(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        before = trip.plan
        await trip.turn("move the trip a week later")
        return before, trip.plan

    before, after = _run(settings, [("Kyoto", 2), ("Osaka", 2)], body)

    assert after.version == before.version and after.dates == before.dates


def test_moving_a_one_city_trip_is_the_single_city_date_change(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        before = trip.plan
        _save_form_shifted(trip, WEEK)
        await trip.turn("move the trip a week later")
        return before, trip.plan

    before, after = _run(settings, [("Kyoto", 3)], body)

    assert len(after.segments) == 1
    assert (after.dates.start, after.dates.end) == (
        before.dates.start + WEEK,
        before.dates.end + WEEK,
    )
    assert "no longer fits the new route" not in after.change_summary
