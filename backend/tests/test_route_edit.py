"""Changing the route of a multi-city plan: the nights that move, what is refused, and the rules that read a message."""

from __future__ import annotations

import pytest
from plan_samples import make_plan, make_route_plan

from app.application.route_edit import (
    RouteChange,
    SplitStay,
    apply_route_change,
    rule_route_change,
    rule_split_stay,
)
from app.application.route_plan import RouteQuestion


def _route(plan, change: RouteChange) -> tuple[list[tuple[str, int | None]], str]:
    stops, sentence = apply_route_change(plan, change)
    return [(stop.city, stop.nights) for stop in stops], sentence


def _plan(*nights: int):
    """Kyoto, Osaka, Tokyo (as many as there are nights) with the given nights each."""
    plan = make_route_plan(["Kyoto", "Osaka", "Tokyo"][: len(nights)])
    from datetime import timedelta

    from app.domain.models import DateRange

    cursor = plan.dates.start
    for segment, stay in zip(plan.segments, nights):
        segment.dates = DateRange(start=cursor, end=cursor + timedelta(days=stay))
        cursor = segment.dates.end
    return plan


def test_an_added_city_takes_its_nights_from_the_longest_stay() -> None:
    route, sentence = _route(_plan(2, 4, 2), RouteChange(op="add", city="Nara"))

    assert route == [("Kyoto", 2), ("Osaka", 2), ("Tokyo", 2), ("Nara", 2)]
    assert sentence == "Added Nara for 2 nights, taken from Osaka, which now has 2."


def test_an_added_city_goes_after_the_city_named_and_takes_the_nights_asked_for() -> (
    None
):
    route, sentence = _route(
        _plan(2, 5, 2), RouteChange(op="add", city="Nara", after="Kyoto", nights=3)
    )

    assert route == [("Kyoto", 2), ("Nara", 3), ("Osaka", 2), ("Tokyo", 2)]
    assert "taken from Osaka" in sentence


def test_a_city_is_never_added_with_less_than_a_night_or_past_eight() -> None:
    with pytest.raises(RouteQuestion):
        apply_route_change(_plan(1, 1), RouteChange(op="add", city="Nara"))
    with pytest.raises(RouteQuestion):
        apply_route_change(_plan(2, 3), RouteChange(op="add", city="Nara", nights=3))
    with pytest.raises(RouteQuestion, match="up to 8 cities"):
        apply_route_change(
            make_route_plan([f"City{n}" for n in range(8)]),
            RouteChange(op="add", city="Nara"),
        )


def test_a_removed_city_gives_its_nights_to_the_city_before_it_or_after_it_if_it_was_first() -> (
    None
):
    middle, sentence = _route(_plan(2, 3, 2), RouteChange(op="remove", city="Osaka"))
    first, _ = _route(_plan(2, 3, 2), RouteChange(op="remove", city="Kyoto"))

    assert middle == [("Kyoto", 5), ("Tokyo", 2)]
    assert sentence == "Removed Osaka; its 3 nights went to Kyoto."
    assert first == [("Osaka", 5), ("Tokyo", 2)]


def test_the_only_city_cannot_be_removed() -> None:
    with pytest.raises(RouteQuestion, match="only city"):
        apply_route_change(make_plan(), RouteChange(op="remove", city="Kyoto"))


def test_changed_nights_are_taken_from_or_given_to_the_longest_other_stay() -> None:
    more, sentence = _route(
        _plan(2, 4, 2), RouteChange(op="nights", city="Tokyo", nights=3)
    )
    fewer, _ = _route(_plan(2, 4, 2), RouteChange(op="nights", city="Osaka", nights=1))

    assert more == [("Kyoto", 2), ("Osaka", 3), ("Tokyo", 3)]
    assert sentence == "Tokyo now has 3 nights, 1 taken from Osaka, which now has 3."
    assert fewer == [("Kyoto", 5), ("Osaka", 1), ("Tokyo", 2)]


def test_nights_that_leave_another_city_empty_are_asked_about() -> None:
    with pytest.raises(RouteQuestion):
        apply_route_change(
            _plan(1, 1, 1), RouteChange(op="nights", city="Kyoto", nights=2)
        )


def test_reordering_keeps_each_citys_nights() -> None:
    route, _ = _route(
        _plan(2, 3, 4), RouteChange(op="reorder", order=["Tokyo", "Kyoto", "Osaka"])
    )

    assert route == [("Tokyo", 4), ("Kyoto", 2), ("Osaka", 3)]


def test_a_reorder_that_is_not_every_city_is_asked_about() -> None:
    with pytest.raises(RouteQuestion):
        apply_route_change(
            _plan(2, 2, 2), RouteChange(op="reorder", order=["Tokyo", "Kyoto"])
        )


def test_a_city_the_trip_does_not_visit_cannot_be_changed() -> None:
    with pytest.raises(RouteQuestion, match="not one of the cities"):
        apply_route_change(_plan(2, 2), RouteChange(op="remove", city="Paris"))


def test_a_city_visited_twice_asks_which_visit() -> None:
    plan = make_route_plan(["Tokyo", "Kyoto", "Tokyo"])

    with pytest.raises(RouteQuestion, match="You visit Tokyo twice"):
        apply_route_change(plan, RouteChange(op="remove", city="Tokyo"))


def test_the_best_order_is_the_nearest_neighbour_one() -> None:
    plan = _plan(2, 2, 2)
    for segment, (lat, lng) in zip(
        plan.segments, [(35.0, 135.7), (43.0, 141.3), (34.7, 135.5)]
    ):
        from app.domain.models import GeoPoint

        segment.destination.geo = GeoPoint(lat=lat, lng=lng)

    route, _ = _route(plan, RouteChange(op="reorder"))

    assert [city for city, _ in route] == ["Kyoto", "Tokyo", "Osaka"]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("add Nara to the trip", RouteChange(op="add", city="Nara")),
        ("Add Osaka after Kyoto", RouteChange(op="add", city="Osaka", after="Kyoto")),
        ("also add Nara for 2 nights", RouteChange(op="add", city="Nara", nights=2)),
        ("remove Osaka", RouteChange(op="remove", city="Osaka")),
        ("let's skip Tokyo", RouteChange(op="remove", city="Tokyo")),
        ("3 nights in Kyoto", RouteChange(op="nights", city="Kyoto", nights=3)),
        (
            "swap Kyoto and Tokyo",
            RouteChange(op="reorder", order=["Tokyo", "Osaka", "Kyoto"]),
        ),
        ("what is the best order for the cities", RouteChange(op="reorder")),
    ],
)
def test_the_rules_read_a_route_change(message: str, expected: RouteChange) -> None:
    assert rule_route_change(message, _plan(2, 2, 2)) == expected


@pytest.mark.parametrize(
    "message",
    [
        "add a museum to day 2",
        "add Fushimi Inari to day 2",
        "remove the Gion walk",
        "remove Paris",
        "slower pace",
        "3 nights in Paris",
    ],
)
def test_the_rules_leave_everything_else_alone(message: str) -> None:
    assert rule_route_change(message, _plan(2, 2, 2)) is None


def test_a_different_hotel_for_the_last_nights_is_a_split_stay() -> None:
    plan = _plan(2, 4, 2)

    assert rule_split_stay(
        "a different hotel for the last two nights in Osaka", plan
    ) == SplitStay(city="Osaka", last_nights=2)
    assert rule_split_stay("another hotel for the last 3 nights", plan) == SplitStay(
        city=None, last_nights=3
    )
    assert rule_split_stay("a cheaper hotel in Osaka", plan) is None
