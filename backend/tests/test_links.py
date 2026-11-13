"""Links on stops, stays and transport: built by code from fields a source gave, never written by a model."""

from __future__ import annotations

from datetime import datetime

import pytest
from plan_samples import make_plan, make_two_city_plan

from app.application.links import links_for_place, links_for_stay, links_for_ticket
from app.domain.models import (
    GeoPoint,
    HotelStay,
    Link,
    SourceRef,
    TicketOption,
    TripPlan,
)

MAPS = "https://maps.google.com/?cid=123"


def _ref(title: str, url: str) -> dict:
    return {"title": title, "url": url, "source": "google-places"}


def _kinds(links: list[Link]) -> list[tuple[str, str]]:
    return [(link.kind, link.url) for link in links]


def test_a_place_gets_its_website_its_maps_page_and_its_booking_link() -> None:
    row = {
        "name": "Fushimi Inari",
        "geo": GeoPoint(lat=34.97, lng=135.77),
        "booking_url": "https://tickets.example.com/inari",
        "source_refs": [
            _ref("Fushimi Inari", MAPS),
            _ref("Fushimi Inari website", "https://inari.example.jp"),
        ],
    }

    assert _kinds(links_for_place(row)) == [
        ("booking", "https://tickets.example.com/inari"),
        ("official", "https://inari.example.jp"),
        ("map", MAPS),
    ]


def test_a_place_without_a_maps_page_gets_one_built_from_its_coordinates_only() -> None:
    links = links_for_place(
        {"name": "Gion", "geo": GeoPoint(lat=35.0037, lng=135.7752), "source_refs": []}
    )

    assert _kinds(links) == [
        ("map", "https://www.google.com/maps/search/?api=1&query=35.0037,135.7752")
    ]
    assert "Gion" not in links[0].url


def test_a_place_with_nothing_has_no_links() -> None:
    assert (
        links_for_place(
            {
                "name": "Nowhere",
                "source_refs": [_ref("A page", "https://example.com/page")],
            }
        )
        == []
    )


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "ftp://example.com/x",
        "data:text/html,hi",
        "//example.com",
        "not a url",
        "",
    ],
)
def test_only_http_and_https_links_are_kept(url: str) -> None:
    row = {
        "name": "X",
        "booking_url": url,
        "source_refs": [_ref("X website", url), _ref("X", url)],
    }

    assert links_for_place(row) == []


def test_the_same_address_is_listed_once() -> None:
    row = {
        "name": "X",
        "booking_url": "https://a.example.com",
        "source_refs": [_ref("X website", "https://a.example.com")],
    }

    assert _kinds(links_for_place(row)) == [("booking", "https://a.example.com")]


def test_a_stay_gets_its_booking_link_and_a_map_from_its_coordinates() -> None:
    stay = (
        make_plan().hotels[0].model_copy(update={"geo": GeoPoint(lat=35.0, lng=135.76)})
    )

    assert _kinds(links_for_stay(stay)) == [
        ("booking", "https://example.com/hotel"),
        ("map", "https://www.google.com/maps/search/?api=1&query=35.0,135.76"),
    ]
    assert (
        links_for_stay(
            stay.model_copy(update={"booking_url": "javascript:x", "geo": None})
        )
        == []
    )


def test_a_flight_links_to_its_booking_and_an_estimated_leg_to_its_directions() -> None:
    flight = TicketOption(
        mode="flight", name="CX", booking_url="https://flights.example.com/q"
    )
    train = TicketOption(
        mode="train",
        name="Train",
        estimated=True,
        booking_url="https://www.google.com/maps/dir/?api=1&origin=A&destination=B",
    )

    assert _kinds(links_for_ticket(flight)) == [
        ("booking", "https://flights.example.com/q")
    ]
    assert _kinds(links_for_ticket(train)) == [
        ("map", "https://www.google.com/maps/dir/?api=1&origin=A&destination=B")
    ]
    assert links_for_ticket(TicketOption(mode="flight", name="No link")) == []


def test_a_plan_carries_links_wherever_it_has_a_source_for_them() -> None:
    plan = make_plan(alternatives=2)

    assert [link.kind for link in plan.hotels[0].links] == ["booking"]
    assert [link.kind for link in plan.tickets[0].links] == ["booking"]
    meal = next(item for _, item in plan.iter_items() if item.alternatives)
    assert all(
        link.url.startswith("https://")
        for _, item in plan.iter_items()
        for link in item.links
    )
    assert meal.links is not None


def test_the_links_follow_the_fields_when_an_object_changes_and_the_plan_is_saved_again() -> (
    None
):
    plan = make_plan()
    plan.hotels[0].booking_url = "https://other.example.com/room"

    plan.refresh_links()

    assert plan.hotels[0].links[0].url == "https://other.example.com/room"


def test_a_plan_stored_without_links_loads_with_them() -> None:
    stored = make_two_city_plan().model_dump(mode="json")
    for stay in stored["hotels"]:
        stay.pop("links", None)
        stay["booking_url"] = "https://example.com/hotel"

    loaded = TripPlan.model_validate(stored)

    assert [stay.links[0].kind for stay in loaded.hotels] == ["booking", "booking"]


def test_every_link_in_an_offline_planned_trip_starts_with_https() -> None:
    from multi_city_rig import route_context, run_turn

    from app.config import Settings

    _, plan, _ = run_turn(
        Settings(_env_file=None), route_context("s", [("Kyoto", 2), ("Osaka", 2)])
    )
    links = [
        link
        for obj in [
            *(item for _, item in plan.iter_items()),
            *(alt for _, item in plan.iter_items() for alt in item.alternatives),
            *plan.hotels,
            *plan.hotel_options,
            *plan.tickets,
        ]
        for link in obj.links
    ]

    assert links and all(link.url.startswith("https://") for link in links)
    assert datetime and SourceRef and HotelStay


def test_derived_links_are_not_a_source_a_repair_brought() -> None:
    from app.application.validation import _new_urls

    plan = make_plan()
    stop = plan.days[0].items[0]
    stop.geo = None
    plan.refresh_derived()
    repaired = plan.model_copy(deep=True)
    repaired.days[0].items[0].geo = GeoPoint(lat=35.0, lng=135.7)
    repaired.refresh_derived()

    assert any(link.kind == "map" for link in repaired.days[0].items[0].links)
    assert _new_urls(plan, repaired) == set()
