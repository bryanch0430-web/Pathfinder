"""A version is a number inside one trip, and a session shows its newest trip: the list offers those versions only.

Planning again in the same tab starts a new trip at version 1. The list of versions used to offer the new
trip's v1 and the old trip's v2 and v1 together (the page showed "v1, v2, v1"), and a number asked for with
`?version=` was looked up in every trip, so the old trip's versions could not be reached by their number.
"""

from __future__ import annotations

import random
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.models import TripPlan
from app.infrastructure.store import SQLiteStore
from app.interface.api import create_app
from scripts.seed_platform_demo import DESTINATIONS, _plan
from tests.test_modify_dates import run_trip

SESSION = "sess_two_trips"


def _plan_of(destination: str, trip_id: str, version: int, at: int) -> TripPlan:
    place = next(item for item in DESTINATIONS if item["name"] == destination)
    plan = _plan(random.Random(1), place, 1).model_copy(update={"session_id": SESSION, "trip_id": trip_id, "version": version})
    return plan.model_copy(update={"updated_at": plan.updated_at + timedelta(seconds=at)})


def _two_trips(store) -> None:
    """Tokyo with two versions, then the traveller plans again: Kyoto at version 1."""
    store.save_plan(_plan_of("Tokyo", "trip_tokyo", 1, 0), "Initial plan")
    store.save_plan(_plan_of("Tokyo", "trip_tokyo", 2, 10), "Swapped the hotel")
    store.save_plan(_plan_of("Kyoto", "trip_kyoto", 1, 60), "Initial plan")


def _listed(store) -> list[tuple[str, int]]:
    return [(row["destination"], row["version"]) for row in store.list_versions(SESSION)]


def test_the_list_offers_the_versions_of_the_newest_trip_only(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "plans.db")
    _two_trips(store)

    assert _listed(store) == [("Kyoto", 1)]


def test_a_number_asked_for_is_a_version_of_the_trip_the_session_shows(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "plans.db")
    _two_trips(store)

    assert store.get_plan(SESSION, 1).destination.name == "Kyoto"
    assert store.get_plan(SESSION, 2) is None
    assert store.get_plan(SESSION).destination.name == "Kyoto"


def test_every_listed_version_is_found_by_its_number_and_none_is_listed_twice(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "plans.db")
    _two_trips(store)
    store.save_plan(_plan_of("Kyoto", "trip_kyoto", 2, 90), "Swapped the hotel")
    store.save_plan(_plan_of("Kyoto", "trip_kyoto", 3, 120), "Moved the dates")

    listed = store.list_versions(SESSION)

    assert [row["version"] for row in listed] == [3, 2, 1]
    assert {row["trip_id"] for row in listed} == {"trip_kyoto"}
    for row in listed:
        found = store.get_plan(SESSION, row["version"])
        assert (found.trip_id, found.version) == (row["trip_id"], row["version"])


def test_a_session_with_one_trip_lists_all_of_its_versions_newest_first(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "plans.db")
    store.save_plan(_plan_of("Tokyo", "trip_tokyo", 1, 0), "Initial plan")
    store.save_plan(_plan_of("Tokyo", "trip_tokyo", 2, 10), "Swapped the hotel")

    assert [(row["version"], row["change_summary"]) for row in store.list_versions(SESSION)] == [
        (2, "Swapped the hotel"),
        (1, "Initial plan"),
    ]
    assert store.list_versions("sess_none") == []


def test_a_rewritten_older_version_of_the_old_trip_does_not_bring_it_back(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "plans.db")
    _two_trips(store)

    store.save_plan(_plan_of("Tokyo", "trip_tokyo", 2, 200), None, replace=True)

    assert _listed(store) == [("Kyoto", 1)]
    assert store.get_plan(SESSION).destination.name == "Kyoto"


def test_planning_again_in_the_same_tab_leaves_one_v1_in_the_list(settings: Settings) -> None:
    async def scenario(trip) -> None:
        await trip.turn("Swap the hotel for the other option.")
        assert [row["version"] for row in trip.store.list_versions(trip.session_id)] == [2, 1]

        trip.rig.llm.route = {"intent": "plan", "confidence": 0.95}
        await trip.turn("Plan my trip.")

        listed = trip.store.list_versions(trip.session_id)
        assert [(row["version"], row["change_summary"]) for row in listed] == [(1, "Initial plan")]
        assert trip.store.get_plan(trip.session_id, 1).trip_id == listed[0]["trip_id"] != trip.plan.trip_id
        assert trip.store.get_plan(trip.session_id, 2) is None

    run_trip(settings, scenario)


def test_the_api_lists_and_serves_the_versions_of_the_newest_trip(settings: Settings) -> None:
    with TestClient(create_app(settings)) as client:
        guest = {"X-Client-Token": "guest-two-trips"}
        session_id = client.post("/api/journeys", json={"client_token": "guest-two-trips"}).json()["session"]["id"]
        store = client.app.state.container.store
        for destination, trip_id, version, at, summary in (
            ("Tokyo", "trip_tokyo", 1, 0, "Initial plan"),
            ("Tokyo", "trip_tokyo", 2, 10, "Swapped the hotel"),
            ("Kyoto", "trip_kyoto", 1, 60, "Initial plan"),
        ):
            plan = _plan_of(destination, trip_id, version, at).model_copy(update={"session_id": session_id})
            store.save_plan(plan, summary)

        opened = client.get(f"/api/journeys/{session_id}", headers=guest).json()
        revisions = client.get(f"/api/journeys/{session_id}/revisions", headers=guest).json()["versions"]
        first = client.get(f"/api/journeys/{session_id}/itinerary", params={"version": 1}, headers=guest)
        second = client.get(f"/api/journeys/{session_id}/itinerary", params={"version": 2}, headers=guest)

    assert [row["version"] for row in opened["versions"]] == [1] == [row["version"] for row in revisions]
    assert first.json()["plan"]["destination"]["name"] == "Kyoto"
    assert second.status_code == 404
