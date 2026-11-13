"""The route step: nights for each city, the segments they make, and the nearest-neighbour order."""

from __future__ import annotations

from datetime import date

import pytest

from app.application.route_plan import (
    RouteQuestion,
    allocate_nights,
    build_segments,
    nearest_order,
)
from app.domain.models import Destination, GeoPoint, TripStop


def _stops(*nights: int | None) -> list[TripStop]:
    return [TripStop(city=f"City {n}", nights=value) for n, value in enumerate(nights, 1)]


def test_stated_nights_are_kept_and_the_rest_is_shared() -> None:
    assert allocate_nights(_stops(4, None), 7) == ([4, 3], "")


def test_unstated_nights_are_split_evenly_with_the_remainder_to_the_earlier_cities() -> None:
    assert allocate_nights(_stops(None, None, None), 7) == ([3, 2, 2], "")


def test_nights_the_dates_cover_beyond_those_listed_go_to_the_first_city() -> None:
    nights, note = allocate_nights(_stops(2, 2), 6)

    assert nights == [4, 2]
    assert note == "The dates cover 2 more nights than you listed; I added them to City 1."
    assert allocate_nights(_stops(2, 2), 5)[1] == "The dates cover 1 more night than you listed; I added it to City 1."


def test_nights_that_add_up_to_more_than_the_dates_are_asked_about() -> None:
    with pytest.raises(RouteQuestion) as asked:
        allocate_nights(_stops(5, 5), 7)

    assert asked.value.question == "The nights you gave add up to 10 but the dates cover 7. Which should I follow?"


def test_fewer_nights_than_cities_is_asked_about() -> None:
    with pytest.raises(RouteQuestion) as asked:
        allocate_nights(_stops(None, None, None), 2)

    assert asked.value.question == (
        "The trip has fewer nights than cities. Which cities should I drop, or should the trip be longer?"
    )


def test_no_night_left_for_an_unstated_city_is_asked_about() -> None:
    with pytest.raises(RouteQuestion):
        allocate_nights(_stops(5, None), 5)


def test_segments_follow_each_other_and_share_the_day_one_is_left() -> None:
    places = [Destination(name="Kyoto"), Destination(name="Osaka")]

    segments = build_segments(places, [2, 2], date(2026, 12, 3))

    assert [(s.id, s.destination.name, s.dates.start, s.dates.end) for s in segments] == [
        ("seg_1", "Kyoto", date(2026, 12, 3), date(2026, 12, 5)),
        ("seg_2", "Osaka", date(2026, 12, 5), date(2026, 12, 7)),
    ]


def _at(name: str, lat: float, lng: float) -> Destination:
    return Destination(name=name, geo=GeoPoint(lat=lat, lng=lng))


def test_the_nearest_order_starts_from_the_origin() -> None:
    sapporo, osaka, tokyo = _at("Sapporo", 43.06, 141.35), _at("Osaka", 34.69, 135.5), _at("Tokyo", 35.68, 139.69)

    assert nearest_order(GeoPoint(lat=22.3, lng=114.2), [sapporo, osaka, tokyo]) == [1, 2, 0]
    assert nearest_order(None, [sapporo, osaka, tokyo]) == [0, 2, 1]


def test_a_place_with_no_coordinates_goes_last_in_its_place() -> None:
    places = [_at("Osaka", 34.69, 135.5), Destination(name="Nowhere"), _at("Tokyo", 35.68, 139.69)]

    assert nearest_order(None, places) == [0, 2, 1]
