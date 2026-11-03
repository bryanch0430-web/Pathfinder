"""Which trips an account, an expired sign-in and an old orphan row can reach.

These pin cases the access-control tests of each step did not cover on their own: a trip made after
sign-up with the old guest token, a bearer that has expired, and rows from before tokens were checked.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app.application.auth import create_token
from app.config import Settings
from app.interface.api import create_app

PASSWORD = "correct-horse"


@pytest.fixture()
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _sign_up(client: TestClient, email: str, guest_token: str = "") -> dict:
    response = client.post(
        "/api/account/signup", json={"email": email, "password": PASSWORD, "client_token": guest_token}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return {
        "headers": {"Authorization": f"Bearer {body['token']}"},
        "profile": body["profile_token"],
        "user": body["user"],
    }


def _trip(client: TestClient, token: str, headers: dict[str, str] | None = None) -> str:
    created = client.post("/api/journeys", json={"client_token": token}, headers=headers or {})
    assert created.status_code == 200, created.text
    return created.json()["session"]["id"]


def _opens(client: TestClient, session_id: str, headers: dict[str, str]) -> bool:
    return client.get(f"/api/journeys/{session_id}", headers=headers).status_code == 200


def test_a_trip_made_with_the_guest_token_after_sign_up_is_taken_by_the_next_sign_in(client: TestClient) -> None:
    """Another window that never heard of the sign-in keeps making trips with the guest token."""
    first = _trip(client, "guest-two-windows")
    account = _sign_up(client, "windows@example.com", "guest-two-windows")
    assert _opens(client, first, account["headers"])

    later = _trip(client, "guest-two-windows")

    guest = {"X-Client-Token": "guest-two-windows"}
    assert _opens(client, later, guest) and not _opens(client, later, account["headers"])
    signed_in = client.post(
        "/api/account/signin",
        json={"email": "windows@example.com", "password": PASSWORD, "client_token": "guest-two-windows"},
    )
    assert signed_in.status_code == 200
    assert _opens(client, later, account["headers"]) and not _opens(client, later, guest)


def test_an_expired_sign_in_opens_no_account_trip_and_no_profile(client: TestClient, settings: Settings) -> None:
    account = _sign_up(client, "expired@example.com")
    trip = _trip(client, account["profile"], account["headers"])
    expired = create_token(account["user"]["id"], account["user"]["email"], settings.auth_secret, hours=-1)
    stale = {"Authorization": f"Bearer {expired}"}
    as_profile = {"X-Client-Token": account["profile"], **stale}
    assert _opens(client, trip, account["headers"])

    # The trip answers 404, as for a stranger, and none of its actions work.
    for headers in (stale, as_profile):
        assert not _opens(client, trip, headers)
        assert client.delete(f"/api/journeys/{trip}", headers=headers).status_code == 404
        assert client.post(f"/api/journeys/{trip}/share", headers=headers).status_code == 404
        turn = client.post("/api/turn/stream", json={"session_id": trip, "message": "hi"}, headers=headers)
        assert turn.status_code == 404
    # The account's profile needs a bearer that is still good.
    for url in ("/api/traveller", "/api/wishlist", "/api/popular"):
        assert client.get(url, headers=as_profile).status_code == 401, url
    assert client.patch("/api/traveller", json={"preferences": ["x"]}, headers=as_profile).status_code == 401
    assert client.post("/api/recall/clear", headers=as_profile).status_code == 401
    # Nor can a trip be made for the profile, with or without a body token.
    assert client.post("/api/journeys", json={"client_token": account["profile"]}, headers=stale).status_code == 401
    assert client.post("/api/journeys", json={}, headers=as_profile).status_code == 401
    # The public list does not need an identity at all.
    assert client.get("/api/popular", headers=stale).status_code == 200
    assert _opens(client, trip, account["headers"])  # nothing was deleted


def test_a_row_with_no_token_opens_for_nobody_however_the_token_is_left_out(client: TestClient) -> None:
    """Rows from before tokens were checked may have an empty token. An empty value proves nothing."""
    account = _sign_up(client, "orphan@example.com")
    orphan = client.app.state.container.store.create_session("").id

    for headers in ({}, {"X-Client-Token": ""}, {"X-Client-Token": " "}, account["headers"]):
        assert not _opens(client, orphan, headers), headers
        assert client.delete(f"/api/journeys/{orphan}", headers=headers).status_code == 404
    for token in ("", None):
        body = {"session_id": orphan} if token is None else {"session_id": orphan, "client_token": token}
        assert client.post("/api/account/attach", json=body, headers=account["headers"]).status_code == 404
    with closing(sqlite3.connect(client.app.state.container.store.path)) as conn:
        assert conn.execute("SELECT user_id FROM journeys WHERE id=?", (orphan,)).fetchone() == ("",)


def test_a_profile_token_on_a_row_with_no_account_opens_it_for_nobody(client: TestClient) -> None:
    """A `user:` token that no bearer proved owns nothing, so naming it in a header must not open the row."""
    legacy = client.app.state.container.store.create_session("user:user_legacy000001").id
    account = _sign_up(client, "legacy@example.com")

    for headers in (
        {"X-Client-Token": "user:user_legacy000001"},
        {"X-Client-Token": "user:user_legacy000001", **account["headers"]},
    ):
        assert not _opens(client, legacy, headers), headers
        assert client.delete(f"/api/journeys/{legacy}", headers=headers).status_code == 404
        patched = client.patch(f"/api/journeys/{legacy}/state", json={"destination": "Kyoto"}, headers=headers)
        assert patched.status_code == 404
    assert client.app.state.container.store.get_session(legacy) is not None


def test_a_profile_token_cannot_claim_a_row_with_no_account_either(client: TestClient) -> None:
    """`attach` moves what its token proves, and a `user:` token that no bearer proved proves nothing."""
    legacy = client.app.state.container.store.create_session("user:user_legacy000001").id
    claimer = _sign_up(client, "claimer@example.com")

    claimed = client.post(
        "/api/account/attach",
        json={"session_id": legacy, "client_token": "user:user_legacy000001"},
        headers=claimer["headers"],
    )

    assert claimed.status_code == 404
    assert not _opens(client, legacy, claimer["headers"])
    with closing(sqlite3.connect(client.app.state.container.store.path)) as conn:
        assert conn.execute("SELECT user_id FROM journeys WHERE id=?", (legacy,)).fetchone() == ("",)


def test_a_trip_made_by_an_account_does_not_borrow_a_guest_token(client: TestClient) -> None:
    """Naming someone else's guest token must not add a trip to that guest's profile."""
    guest = {"X-Client-Token": "guest-whose-profile"}
    _trip(client, "guest-whose-profile")
    assert client.get("/api/traveller", headers=guest).json()["sessions"] == 1
    account = _sign_up(client, "borrower@example.com")

    made = _trip(client, "guest-whose-profile", account["headers"])

    assert client.get("/api/traveller", headers=guest).json()["sessions"] == 1
    assert _opens(client, made, account["headers"]) and not _opens(client, made, guest)
    with closing(sqlite3.connect(client.app.state.container.store.path)) as conn:
        stored = conn.execute("SELECT client_token, user_id FROM journeys WHERE id=?", (made,)).fetchone()
    assert stored == (account["profile"], account["user"]["id"])
