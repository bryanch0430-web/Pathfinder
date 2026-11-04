"""What an edit fetched again is no longer marked outdated, and the flight reservation follows the flight.

Before, "move the trip a week later" fetched a new forecast and the plan kept the stored "outdated" mark for the
weather (only the next reopen cleared it), and it kept the reservation entry of the flight it had replaced or dropped.
"""

from __future__ import annotations

from datetime import timedelta

from app.config import Settings
from app.domain.models import FreshnessMark, TripPlan, utcnow
from tests.test_modify_dates import WEEK_LATER, no_flights
from tests.test_modify_targets import _modify

LONG_AGO = timedelta(days=400)


def _outdated(section: str) -> FreshnessMark:
    return FreshnessMark(
        section=section,  # type: ignore[arg-type]
        status="outdated",
        fetched_at=utcnow() - LONG_AGO,
        checked_at=utcnow(),
        window_seconds=3600,
        note="The source could not be reached; the earlier data is kept.",
    )


def _stale_weather(plan: TripPlan) -> None:
    for day in plan.days:
        if day.weather is not None:
            day.weather.fetched_at = utcnow() - LONG_AGO
    plan.data_quality.freshness = [_outdated("weather")]


def test_an_edit_that_fetched_a_new_forecast_drops_the_outdated_mark_of_the_weather(settings: Settings) -> None:
    _, updated, _ = _modify(
        settings, "move the trip a week later", ["dates"], new_range=WEEK_LATER, prepare=_stale_weather
    )

    assert all(day.weather is not None for day in updated.days)
    assert [mark.section for mark in updated.data_quality.freshness] == []


def test_an_edit_that_did_not_fetch_the_weather_keeps_its_outdated_mark(settings: Settings) -> None:
    _, updated, _ = _modify(settings, "swap the hotel for another one", ["hotel"], prepare=_stale_weather)

    assert [(mark.section, mark.status) for mark in updated.data_quality.freshness] == [("weather", "outdated")]


def test_the_reservation_of_a_flight_the_edit_dropped_is_not_kept(settings: Settings) -> None:
    plan, updated, _ = _modify(
        settings, "move the trip a week later", ["dates"], new_range=WEEK_LATER, break_registry=no_flights
    )

    assert [r.name for r in plan.reservations if r.name.startswith("Flight")], "the plan had one"
    assert not updated.tickets
    assert not [r.name for r in updated.reservations if r.name.startswith("Flight")]


class _OtherAir:
    """A flight source whose only option is another airline's, with its own booking link."""

    name = "other-air"
    available = True

    async def search(self, *, origin, destination, depart_date, return_date, party_size, currency):
        from datetime import datetime

        from app.domain.models import Money, PriceKind
        from app.infrastructure.providers.flights import FlightCandidate

        depart = datetime.combine(depart_date, datetime.min.time()).replace(hour=11)
        return [
            FlightCandidate(
                airline="Other Air", flight_number="OA7", origin=origin[:3].upper(), destination=destination[:3].upper(),
                depart_at=depart, arrive_at=depart + timedelta(hours=4), duration_minutes=240,
                price=Money(amount=300, currency="USD"), price_kind=PriceKind.REFERENCE,
                booking_url="https://example.com/other-air", source_refs=[],
            )
        ]


def test_the_reservation_follows_the_flight_that_replaced_the_old_one(settings: Settings) -> None:
    plan, updated, _ = _modify(
        settings,
        "move the trip a week later",
        ["dates"],
        new_range=WEEK_LATER,
        break_registry=lambda registry: setattr(registry, "flights", [_OtherAir()]),
    )

    assert [ticket.name for ticket in plan.tickets] != [ticket.name for ticket in updated.tickets]
    flight_rows = [(r.name, r.url) for r in updated.reservations if r.name.startswith("Flight")]
    assert flight_rows == [("Flight · Other Air OA7", "https://example.com/other-air")]


def test_the_other_reservations_are_left_alone(settings: Settings) -> None:
    def book_a_place(plan: TripPlan) -> None:
        from app.domain.models import Reservation

        plan.reservations.append(Reservation(name="Ghibli Museum", reason="Timed entry", url="https://example.com/g", status="booked"))

    _, updated, _ = _modify(
        settings, "move the trip a week later", ["dates"], new_range=WEEK_LATER, prepare=book_a_place, break_registry=no_flights
    )

    assert [(r.name, r.status) for r in updated.reservations if not r.name.startswith("Flight")] == [
        ("Ghibli Museum", "booked")
    ]
