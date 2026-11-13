"""The map payload of a trip: the old keys as they were, and the cities and stays for a map of several cities."""

from __future__ import annotations

from plan_samples import make_plan, make_two_city_plan

from app.domain.models import GeoPoint
from app.interface.api import map_payload


def test_a_trip_of_one_city_keeps_its_centre_and_hotel_and_has_one_segment() -> None:
    plan = make_plan()
    plan.destination.geo = GeoPoint(lat=35.01, lng=135.77)
    plan.hotels[0].geo = GeoPoint(lat=35.0, lng=135.76)

    payload = map_payload(plan)

    assert payload["center"] == {"lat": 35.01, "lng": 135.77}
    assert payload["hotel"] == {"name": plan.hotels[0].name, "lat": 35.0, "lng": 135.76}
    assert [(s["id"], s["name"], s["nights"]) for s in payload["segments"]] == [
        ("seg_1", "Kyoto", 1)
    ]
    assert [(s["id"], s["segment_id"]) for s in payload["stays"]] == [
        ("hotel_1", "seg_1")
    ]


def test_a_trip_of_two_cities_lists_both_and_each_stay() -> None:
    plan = make_two_city_plan()
    plan.segments[0].destination.geo = GeoPoint(lat=35.01, lng=135.77)
    plan.hotels[1].geo = GeoPoint(lat=34.7, lng=135.51)

    payload = map_payload(plan)

    assert [
        (s["id"], s["name"], s["start"], s["end"], s["nights"])
        for s in payload["segments"]
    ] == [
        ("seg_a", "Kyoto", "2026-12-03", "2026-12-05", 2),
        ("seg_b", "Osaka", "2026-12-05", "2026-12-07", 2),
    ]
    assert (
        payload["segments"][0]["center"] == {"lat": 35.01, "lng": 135.77}
        and payload["segments"][1]["center"] is None
    )
    assert [
        (s["id"], s["segment_id"], s["lat"], s["check_in"]) for s in payload["stays"]
    ] == [
        ("hotel_a", "seg_a", None, "2026-12-03"),
        ("hotel_b", "seg_b", 34.7, "2026-12-05"),
    ]
    assert payload["hotel"] is None or payload["hotel"]["name"] == plan.hotels[0].name


def test_the_version_list_names_the_route_of_a_trip_of_several_cities(tmp_path) -> None:
    from app.infrastructure.store import SQLiteStore

    store = SQLiteStore(tmp_path / "t.db")
    session, other = store.create_session("guest-versions"), store.create_session("guest-versions-2")
    two, one = make_two_city_plan(), make_plan()
    two.session_id, one.session_id = session.id, other.id
    store.save_plan(two, "Two cities")
    store.save_plan(one, "One city")

    assert [row["destination"] for row in store.list_versions(session.id)] == ["Kyoto → Osaka"]
    assert [row["destination"] for row in store.list_versions(other.id)] == ["Kyoto"]
