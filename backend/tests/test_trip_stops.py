"""The trip form holds up to eight cities in order, each with its nights."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.models import TRIP_STOPS_MAX, TripContext, TripStop


def _context(**fields) -> TripContext:
    return TripContext(session_id="s1", **fields)


def test_a_context_stored_without_stops_is_one_stop_at_its_destination() -> None:
    stored = _context(destination="Kyoto").model_dump(mode="json")
    stored.pop("stops", None)

    context = TripContext.model_validate(stored)

    assert context.stops == []
    assert context.route == [TripStop(city="Kyoto")]


def test_a_context_with_no_destination_has_no_route() -> None:
    assert _context().route == []


def test_stops_round_trip_in_order() -> None:
    context = _context(
        destination="Tokyo",
        stops=[TripStop(city="Tokyo", nights=4), TripStop(city="Kyoto")],
    )

    again = TripContext.model_validate(context.model_dump(mode="json"))

    assert [(stop.city, stop.nights) for stop in again.stops] == [
        ("Tokyo", 4),
        ("Kyoto", None),
    ]
    assert again.route == again.stops


def test_a_form_with_no_stops_dumps_as_it_did_before() -> None:
    assert "stops" not in _context(destination="Kyoto").model_dump(mode="json")


def test_the_city_is_stripped() -> None:
    assert TripStop(city="  Kyoto ").city == "Kyoto"


def test_a_ninth_stop_is_refused() -> None:
    assert TRIP_STOPS_MAX == 8
    _context(stops=[TripStop(city=f"City {n}") for n in range(TRIP_STOPS_MAX)])
    with pytest.raises(ValidationError):
        _context(stops=[TripStop(city=f"City {n}") for n in range(TRIP_STOPS_MAX + 1)])


@pytest.mark.parametrize(
    "bad",
    [
        {"city": "", "nights": 2},
        {"city": "   "},
        {"city": "Kyoto", "nights": 0},
        {"city": "Kyoto", "nights": 61},
    ],
)
def test_a_stop_needs_a_city_and_sensible_nights(bad: dict) -> None:
    with pytest.raises(ValidationError):
        TripStop(**bad)
