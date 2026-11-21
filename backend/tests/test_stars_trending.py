"""Star ratings stay on the trip, and trending exposes only public fields."""
import uuid

import pytest
from httpx import AsyncClient

from app.core.database import get_db
from app.main import app
from app.models.enums import OnboardingStatus, TripStatus
from app.models.trip import Trip
from tests.test_admin_settings import _account, _auth


async def _trip(
    user_id: str, destination: str, useful: bool | None = None, stars: int | None = None,
    places: list[str] | None = None, status: TripStatus = TripStatus.PLANNING,
) -> str:
    preferences: dict = {}
    if useful is not None:
        preferences["useful"] = useful
    if stars is not None:
        preferences["stars"] = stars
        preferences["adults"] = 4
    trip = Trip(
        user_id=user_id, destination=destination, origin_text="Hong Kong", duration_days=4,
        status=status, onboarding_status=OnboardingStatus.COMPLETE, currency="HKD",
        places_to_visit=places or [], planning_preferences=preferences,
    )
    generator = app.dependency_overrides[get_db]()
    session = await generator.__anext__()
    try:
        session.add(trip)
        await session.commit()
        await session.refresh(trip)
        return str(trip.id)
    finally:
        await generator.aclose()


@pytest.mark.asyncio
async def test_a_useful_plan_keeps_its_stars(client: AsyncClient):
    user_id, token = await _account(False)
    trip_id = await _trip(user_id, "Kyoto", status=TripStatus.COMPLETED)
    marked = await client.post(
        f"/api/trips/{trip_id}/feedback", headers=_auth(token), json={"useful": True, "stars": 4},
    )
    assert marked.status_code == 200
    assert marked.json() == {"useful": True, "stars": 4}

    listed = await client.get("/api/trips", headers=_auth(token))
    saved = next(row for row in listed.json() if row["id"] == trip_id)
    assert saved["planning_preferences"]["stars"] == 4

    cleared = await client.post(
        f"/api/trips/{trip_id}/feedback", headers=_auth(token), json={"useful": None},
    )
    assert cleared.json()["useful"] is None
    assert cleared.json()["stars"] is None

    draft = await _trip(user_id, "Osaka")
    early = await client.post(f"/api/trips/{draft}/feedback", headers=_auth(token), json={"useful": True})
    assert early.status_code == 409


def test_planner_run_is_visible_until_the_turn_ends(tmp_path, monkeypatch):
    from app.desk import activity
    monkeypatch.setattr(activity, "_PATH", tmp_path / "runs.json")
    activity.set_planner_run("trip-1", "Reading the message")
    activity.set_planner_run("trip-1", "Checking travel time")
    row = activity.read_planner_run("trip-1")
    assert row["stage"] == "Checking travel time"
    assert row["started_at"]
    activity.set_planner_run("trip-1", None)
    assert activity.read_planner_run("trip-1") is None


def test_public_shape_keeps_sights_and_drops_flights_and_hotels():
    from app.services.trending import public_shape
    stays, sights = public_shape([
        {"day": 1, "base": "Rome", "items": [
            {"name": "Fly Hong Kong to Rome"},
            {"name": "Colosseum"},
            {"name": "Hotel Artemide"},
        ]},
        {"day": 2, "base": "Florence", "items": [{"name": "Uffizi Gallery"}]},
    ])
    assert stays == ["Rome", "Florence"]
    assert sights == ["Colosseum", "Uffizi Gallery"]


@pytest.mark.asyncio
async def test_trending_lists_useful_destinations_without_private_fields(client: AsyncClient):
    user_id, _token = await _account(False)
    await _trip(user_id, "Osaka", useful=True, stars=5, places=["Dotonbori"])
    await _trip(user_id, "Osaka", useful=True, stars=3, places=["Osaka Castle"])
    await _trip(user_id, "Seoul", useful=False, places=["Secret stay"])

    found = await client.get("/api/trips/trending?q=osaka")
    assert found.status_code == 200
    body = found.json()
    assert len(body["items"]) == 1
    card = body["items"][0]
    assert card["destination"] == "Osaka"
    assert card["plans"] == 2
    assert card["days"] == 4
    assert card["stars"] == 4.0
    assert set(card["places"]) == {"Dotonbori", "Osaka Castle"}
    assert "origin" not in card
    assert "adults" not in card
    dumped = str(body)
    assert "Hong Kong" not in dumped
    assert "Secret stay" not in dumped
