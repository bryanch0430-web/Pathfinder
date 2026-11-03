"""A trip is made with a guest token the caller can use again, or it is not made.

A guest trip answers only to the token it was created with. A trip created with an empty, padded,
control-character or non-ASCII token was kept in the database but could never be opened, claimed or
deleted by anyone, and a client that sent the token only as the X-Client-Token header got one of these.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.interface.api import create_app

PASSWORD = "correct-horse"


class QuickTurns:
    """Stands in for the turn service so these tests never plan."""

    async def handle_turn(self, **_: object):
        yield SimpleNamespace(event="done", data={"ok": True})


@pytest.fixture()
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        test_client.app.state.container.turn_service = QuickTurns()
        yield test_client


def _rows(client: TestClient) -> list[tuple[str, str]]:
    """(client_token, user_id) of every stored session."""
    with closing(sqlite3.connect(client.app.state.container.store.path)) as conn:
        return conn.execute("SELECT client_token, user_id FROM journeys").fetchall()


def _signup(client: TestClient, email: str = "owner@example.com") -> dict[str, str]:
    response = client.post("/api/account/signup", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_a_session_made_with_the_header_alone_opens_with_it(client: TestClient) -> None:
    guest = {"X-Client-Token": "guest-header-only"}

    created = client.post("/api/journeys", json={}, headers=guest)

    assert created.status_code == 200, created.text
    session_id = created.json()["session"]["id"]
    assert client.get(f"/api/journeys/{session_id}", headers=guest).status_code == 200
    assert client.get(f"/api/journeys/{session_id}", headers={"X-Client-Token": "guest-else"}).status_code == 404
    assert _rows(client) == [("guest-header-only", "")]


def test_a_turn_that_starts_a_trip_takes_the_token_from_the_header_too(client: TestClient) -> None:
    started = client.post("/api/turn/stream", json={"message": "Plan Kyoto"}, headers={"X-Client-Token": "guest-turn"})

    assert started.status_code == 200, started.text
    assert _rows(client) == [("guest-turn", "")]


def test_an_explicit_token_wins_over_the_header_when_a_session_is_made(client: TestClient) -> None:
    both = {"X-Client-Token": "guest-head"}
    created = client.post("/api/journeys", json={"client_token": "guest-body"}, headers=both)

    session_id = created.json()["session"]["id"]
    assert client.get(f"/api/journeys/{session_id}", headers={"X-Client-Token": "guest-body"}).status_code == 200
    assert client.get(f"/api/journeys/{session_id}", headers={"X-Client-Token": "guest-head"}).status_code == 404


UNUSABLE = [
    " padded ", "line\nbreak", "trailing\n", "tab\there", "two words", "日本旅行", "nul\x00", "del\x7f", "x" * 121,
]


@pytest.mark.parametrize("token", UNUSABLE, ids=lambda token: repr(token)[:24])
def test_a_token_nobody_could_present_again_makes_no_session(client: TestClient, token: str) -> None:
    on_create = client.post("/api/journeys", json={"client_token": token})
    on_turn = client.post("/api/turn/stream", json={"message": "Plan Kyoto", "client_token": token})

    assert on_create.status_code == 422, on_create.text
    assert on_turn.status_code == 422, on_turn.text
    assert _rows(client) == []


@pytest.mark.parametrize("token", ["bad\x01token", "y" * 121], ids=["control", "too-long"])
def test_an_unusable_header_token_makes_no_session_either(client: TestClient, token: str) -> None:
    assert client.post("/api/journeys", json={}, headers={"X-Client-Token": token}).status_code == 422
    assert client.post("/api/turn/stream", json={"message": "hi"}, headers={"X-Client-Token": token}).status_code == 422
    assert _rows(client) == []


def test_the_longest_token_and_the_usual_ones_are_accepted(client: TestClient) -> None:
    for token in ("x" * 120, "9b2f1c3e-5d4a-4f6b-8a7c-0e1d2c3b4a59", "user:user_abc123", "a.b~c!d"):
        created = client.post("/api/journeys", json={"client_token": token})
        # A profile token needs its bearer; every other shape is a guest token.
        assert created.status_code == (401 if token.startswith("user:") else 200), (token, created.text)


def test_a_turn_cannot_start_a_trip_without_any_identity(client: TestClient) -> None:
    for body, headers in (
        ({"message": "Plan Kyoto"}, {}),
        ({"message": "Plan Kyoto", "client_token": ""}, {"X-Client-Token": ""}),
    ):
        refused = client.post("/api/turn/stream", json=body, headers=headers)
        assert refused.status_code == 401, refused.text
    assert _rows(client) == []


def test_an_account_needs_no_guest_token_to_start_a_trip(client: TestClient) -> None:
    account = _signup(client)

    created = client.post("/api/journeys", json={}, headers=account)
    started = client.post("/api/turn/stream", json={"message": "Plan Kyoto"}, headers=account)

    assert created.status_code == 200 and started.status_code == 200
    session_id = created.json()["session"]["id"]
    assert client.get(f"/api/journeys/{session_id}", headers=account).status_code == 200
    assert [user_id != "" for _, user_id in _rows(client)] == [True, True]


# -- every token field has the same size cap ---------------------------------------


def test_every_request_model_that_takes_a_client_token_caps_its_size() -> None:
    from pydantic import BaseModel

    from app.interface import api

    models = [
        model
        for model in vars(api).values()
        if isinstance(model, type)
        and issubclass(model, BaseModel)
        and model.__module__ == api.__name__  # the request models, not the domain models it imports
        and "client_token" in model.model_fields
    ]
    assert len(models) >= 7, models  # the scan must see the models, or the loop below would pass vacuously
    for model in models:
        caps = [getattr(rule, "max_length", None) for rule in model.model_fields["client_token"].metadata]
        assert api.TOKEN_MAX_LENGTH in caps, f"{model.__name__}.client_token has no size cap"


OVERSIZE = "t" * 121
CREDENTIALS = {"email": "cap@example.com", "password": "correct-horse"}
CALLS = [
    ("post", "/api/journeys", {"json": {"client_token": OVERSIZE}}),
    ("post", "/api/turn/stream", {"json": {"message": "hi", "client_token": OVERSIZE}}),
    ("post", "/api/journeys/sess_x/rating", {"json": {"useful": True, "client_token": OVERSIZE}}),
    ("post", "/api/account/attach", {"json": {"session_id": "sess_x", "client_token": OVERSIZE}}),
    ("post", "/api/account/signup", {"json": {**CREDENTIALS, "client_token": OVERSIZE}}),
    ("post", "/api/account/signin", {"json": {**CREDENTIALS, "client_token": OVERSIZE}}),
    ("patch", "/api/traveller", {"json": {"client_token": OVERSIZE, "preferences": []}}),
    ("post", "/api/recall/clear", {"json": {"client_token": OVERSIZE}}),
    ("get", "/api/traveller", {"params": {"client_token": OVERSIZE}}),
    ("get", "/api/wishlist", {"params": {"client_token": OVERSIZE}}),
    ("get", "/api/popular", {"params": {"client_token": OVERSIZE}}),
]


@pytest.mark.parametrize(("method", "url", "kwargs"), CALLS, ids=[f"{method} {url}" for method, url, _ in CALLS])
def test_a_token_over_the_cap_is_refused_on_every_route_that_takes_one(
    client: TestClient, method: str, url: str, kwargs: dict
) -> None:
    assert getattr(client, method)(url, **kwargs).status_code == 422


def test_a_megabyte_token_is_refused_and_stores_nothing(client: TestClient) -> None:
    before = client.app.state.container.store.path.stat().st_size

    refused = [client.post("/api/journeys", json={"client_token": "A" * 1_000_000}) for _ in range(3)]

    assert [response.status_code for response in refused] == [422, 422, 422]
    assert _rows(client) == []
    assert client.app.state.container.store.path.stat().st_size == before
