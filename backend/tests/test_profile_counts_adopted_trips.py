"""The Settings page counts the trips the account holds, including the ones it adopted at sign-in.

An adopted trip keeps the guest token it was made with and gains the account, so counting by token
alone showed an account with zero chats and no destinations right after it took its guest trips.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.models import TripContext
from app.interface.api import create_app

PASSWORD = "correct-horse"


@pytest.fixture()
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _trip(client: TestClient, token: str, destination: str) -> str:
    session_id = client.post("/api/journeys", json={"client_token": token}).json()["session"]["id"]
    store = client.app.state.container.store
    context = store.get_context(session_id) or TripContext(session_id=session_id)
    store.save_context(context.model_copy(update={"destination": destination}))
    return session_id


def _profile(client: TestClient, headers: dict[str, str]) -> dict:
    response = client.get("/api/traveller", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_guest_profile_counts_its_own_trips(client: TestClient) -> None:
    _trip(client, "guest-count", "Kyoto")
    _trip(client, "guest-count", "Tokyo")

    profile = _profile(client, {"X-Client-Token": "guest-count"})

    assert profile["sessions"] == 2
    assert sorted(profile["destinations"]) == ["Kyoto", "Tokyo"]


def test_the_account_counts_the_trips_it_adopted_and_the_guest_no_longer_does(client: TestClient) -> None:
    _trip(client, "guest-adopted", "Kyoto")
    _trip(client, "guest-adopted", "Tokyo")
    signed_up = client.post(
        "/api/account/signup", json={"email": "counts@example.com", "password": PASSWORD, "client_token": "guest-adopted"}
    ).json()
    account = {"Authorization": f"Bearer {signed_up['token']}"}

    held = _profile(client, account)
    left = _profile(client, {"X-Client-Token": "guest-adopted"})

    assert held["sessions"] == 2 and sorted(held["destinations"]) == ["Kyoto", "Tokyo"]
    assert left["sessions"] == 0 and left["destinations"] == []


def test_a_trip_the_account_starts_after_sign_in_is_counted(client: TestClient) -> None:
    signed_up = client.post("/api/account/signup", json={"email": "later@example.com", "password": PASSWORD}).json()
    account = {"Authorization": f"Bearer {signed_up['token']}"}
    created = client.post("/api/journeys", json={}, headers=account).json()["session"]["id"]
    store = client.app.state.container.store
    store.save_context(TripContext(session_id=created, destination="Osaka"))

    profile = _profile(client, account)

    assert profile["sessions"] == 1 and profile["destinations"] == ["Osaka"]


def test_two_accounts_on_one_browser_do_not_count_each_others_trips(client: TestClient) -> None:
    _trip(client, "guest-shared", "Kyoto")
    first = client.post(
        "/api/account/signup", json={"email": "one@example.com", "password": PASSWORD, "client_token": "guest-shared"}
    ).json()
    second = client.post(
        "/api/account/signup", json={"email": "two@example.com", "password": PASSWORD, "client_token": "guest-shared"}
    ).json()

    mine = _profile(client, {"Authorization": f"Bearer {first['token']}"})
    theirs = _profile(client, {"Authorization": f"Bearer {second['token']}"})

    assert mine["sessions"] == 1 and theirs["sessions"] == 0
