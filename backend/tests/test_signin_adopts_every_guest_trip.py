"""Signing in or up brings every trip of the browser along, not only the one that is open.

A guest trip answers only to the token it was created with. After sign-in the browser sends the
account's identity instead, so a trip that was not moved to the account would stop opening, and the
page used to forget it. Possession of the guest token is the proof for all the trips it made.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.infrastructure.store import SQLiteStore
from app.interface.api import create_app

PASSWORD = "correct-horse"


@pytest.fixture()
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _store(client: TestClient) -> SQLiteStore:
    return client.app.state.container.store


def _trip(client: TestClient, token: str) -> str:
    created = client.post("/api/journeys", json={"client_token": token})
    assert created.status_code == 200, created.text
    return created.json()["session"]["id"]


def _sign_up(client: TestClient, email: str, token: str, session_id: str | None = None) -> dict[str, str]:
    body = {"email": email, "password": PASSWORD, "client_token": token}
    if session_id:
        body["session_id"] = session_id
    response = client.post("/api/account/signup", json=body)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _opens(client: TestClient, session_id: str, headers: dict[str, str]) -> bool:
    return client.get(f"/api/journeys/{session_id}", headers=headers).status_code == 200


def test_sign_up_moves_every_trip_the_guest_token_made(client: TestClient) -> None:
    open_trip, other_trip = _trip(client, "guest-two"), _trip(client, "guest-two")

    account = _sign_up(client, "two@example.com", "guest-two", session_id=open_trip)

    assert _opens(client, open_trip, account) and _opens(client, other_trip, account)
    # They are the account's now: the guest token alone no longer opens them.
    guest = {"X-Client-Token": "guest-two"}
    assert not _opens(client, open_trip, guest) and not _opens(client, other_trip, guest)


def test_sign_in_moves_every_trip_the_guest_token_made(client: TestClient) -> None:
    _sign_up(client, "back@example.com", "an-earlier-browser")
    first, second = _trip(client, "guest-back"), _trip(client, "guest-back")

    response = client.post(
        "/api/account/signin",
        json={"email": "back@example.com", "password": PASSWORD, "client_token": "guest-back", "session_id": first},
    )

    account = {"Authorization": f"Bearer {response.json()['token']}"}
    assert _opens(client, first, account) and _opens(client, second, account)


def test_a_trip_made_with_another_token_stays_with_its_guest(client: TestClient) -> None:
    mine, theirs = _trip(client, "guest-mine"), _trip(client, "guest-theirs")

    account = _sign_up(client, "mine@example.com", "guest-mine", session_id=theirs)

    assert _opens(client, mine, account)
    assert not _opens(client, theirs, account)
    assert _opens(client, theirs, {"X-Client-Token": "guest-theirs"})


def test_a_trip_that_already_belongs_to_an_account_is_not_taken_over(client: TestClient) -> None:
    trip = _trip(client, "shared-browser")
    first = _sign_up(client, "first@example.com", "shared-browser")

    second = _sign_up(client, "second@example.com", "shared-browser")

    assert _opens(client, trip, first) and not _opens(client, trip, second)


def test_an_empty_token_adopts_nothing(client: TestClient) -> None:
    orphan = _trip(client, "")

    _sign_up(client, "empty@example.com", "")

    assert _store(client).get_session(orphan).user_id == ""


def test_a_profile_token_adopts_nothing(client: TestClient) -> None:
    """A ``user:<id>`` token names an account's profile. It is not a guest's proof of ownership."""
    legacy = _store(client).create_session("user:someone-elses-profile")

    _sign_up(client, "profile@example.com", "user:someone-elses-profile")

    assert _store(client).get_session(legacy.id).user_id == ""
