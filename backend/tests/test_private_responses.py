"""A response that depends on who is asking is never stored by a cache.

The guest token and the bearer travel in headers, not in the URL. Two callers therefore send the same
URL, and a shared cache that stores a GET by URL would answer one caller's profile, saved trips or trip
to the next. Those responses say `private, no-store`; the public ones are left alone.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.interface.api import create_app

GUEST = {"X-Client-Token": "guest-cache"}
PRIVATE = "private, no-store"


class QuickTurns:
    """Stands in for the turn service so the stream route never plans."""

    async def handle_turn(self, **_: object):
        yield SimpleNamespace(event="done", data={"ok": True})


@pytest.fixture()
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture()
def session_id(client: TestClient) -> str:
    created = client.post("/api/journeys", json={}, headers=GUEST)
    assert created.status_code == 200, created.text
    return created.json()["session"]["id"]


@pytest.mark.parametrize(
    "url",
    [
        "/api/traveller",
        "/api/wishlist",
        "/api/popular",
        "/api/account/whoami",  # 401 without a bearer: an error answer is not stored either
        "/api/journeys/{session}",
        "/api/journeys/{session}/itinerary",  # 404: the trip has no plan yet
        "/api/journeys/{session}/revisions",
        "/api/journeys/sess_nobody",  # 404
    ],
)
def test_what_depends_on_the_caller_is_not_stored_by_any_cache(client: TestClient, session_id: str, url: str) -> None:
    response = client.get(url.format(session=session_id), headers=GUEST)

    assert response.headers.get("cache-control") == PRIVATE, (response.status_code, dict(response.headers))


def test_writes_and_sign_in_answers_are_not_stored_either(client: TestClient, session_id: str) -> None:
    answers = [
        client.patch("/api/traveller", json={"preferences": ["food"]}, headers=GUEST),
        client.post("/api/recall/clear", headers=GUEST),
        client.post("/api/account/signup", json={"email": "cache@example.com", "password": "correct-horse"}),
        client.post("/api/account/signin", json={"email": "cache@example.com", "password": "wrong-password"}),
        client.post(f"/api/journeys/{session_id}/rating", json={"useful": False}, headers=GUEST),
    ]

    assert [answer.headers.get("cache-control") for answer in answers] == [PRIVATE] * len(answers)


@pytest.mark.parametrize("url", ["/api/runtime", "/api/ready", "/api/public/trip_nothere", "/api/wallet"])
def test_public_answers_are_left_to_their_own_caching(client: TestClient, url: str) -> None:
    response = client.get(url)

    assert "no-store" not in response.headers.get("cache-control", ""), url


def test_the_stream_keeps_the_cache_header_it_sets_itself(client: TestClient) -> None:
    client.app.state.container.turn_service = QuickTurns()

    streamed = client.post("/api/turn/stream", json={"message": "Plan Kyoto"}, headers=GUEST)

    assert streamed.status_code == 200 and "event: done" in streamed.text
    assert streamed.headers["cache-control"] == "no-cache"
