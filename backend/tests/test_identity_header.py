"""The identity routes read the guest token from the X-Client-Token header, so it stays out of URLs.

GET /api/traveller, /api/wishlist and /api/popular took the token only as a `client_token` query
parameter. A server writes the whole request line to its access log, so the credential that opens a
guest's trips sat in the log. The parameter still works for older clients.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application import trending_rag
from app.config import Settings
from app.interface.api import create_app
from scripts.seed_platform_demo import DESTINATIONS, _plan, _saved_trip

PASSWORD = "correct-horse"
FRONTEND_SRC = Path(__file__).resolve().parents[2] / "frontend" / "src"


@pytest.fixture()
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _signup(client: TestClient, email: str) -> tuple[dict[str, str], str]:
    response = client.post("/api/account/signup", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    body = response.json()
    return {"Authorization": f"Bearer {body['token']}"}, body["profile_token"]


def _seed_trending(client: TestClient) -> None:
    store = client.app.state.container.store
    place = next(item for item in DESTINATIONS if item["name"] == "Kyoto")
    rng = random.Random(11)
    for index in range(10):
        store.save_saved_trip(_saved_trip(rng, _plan(rng, place, 100 + index)))
    trending_rag.invalidate()


def _matches(response) -> list[str]:
    return [tag for item in response.json()["items"] for tag in item.get("match", [])]


def test_the_header_alone_identifies_a_guest_on_every_identity_route(client: TestClient) -> None:
    guest = {"X-Client-Token": "guest-header"}

    saved = client.patch("/api/traveller", json={"preferences": ["food"]}, headers=guest)
    assert saved.status_code == 200, saved.text
    assert saved.json()["profile"]["preferences"] == ["food"]

    profile = client.get("/api/traveller", headers=guest)
    assert profile.status_code == 200, profile.text
    assert profile.json()["preferences"] == ["food"]

    assert client.get("/api/wishlist", headers=guest).status_code == 200

    _seed_trending(client)
    popular = client.get("/api/popular", headers=guest)
    assert popular.status_code == 200 and "food" in _matches(popular)
    # Another guest sees the public list without matching.
    assert _matches(client.get("/api/popular", headers={"X-Client-Token": "guest-else"})) == []

    cleared = client.post("/api/recall/clear", headers=guest)
    assert cleared.status_code == 200 and cleared.json()["preferences"] == 1
    assert client.get("/api/traveller", headers=guest).json()["preferences"] == []


def test_the_query_parameter_still_works_and_an_explicit_token_wins_over_the_header(client: TestClient) -> None:
    client.patch("/api/traveller", json={"client_token": "guest-old", "preferences": ["temples"]})

    older_client = client.get("/api/traveller", params={"client_token": "guest-old"})
    assert older_client.json()["preferences"] == ["temples"]

    both = client.get(
        "/api/traveller", params={"client_token": "guest-old"}, headers={"X-Client-Token": "guest-other"}
    )
    assert both.json()["preferences"] == ["temples"]


def test_no_token_anywhere_is_still_refused(client: TestClient) -> None:
    for headers in ({}, {"X-Client-Token": ""}, {"X-Client-Token": "   "}):
        assert client.get("/api/traveller", headers=headers).status_code == 401
        assert client.get("/api/wishlist", headers=headers).status_code == 401
        assert client.post("/api/recall/clear", headers=headers).status_code == 401
        assert client.patch("/api/traveller", json={"preferences": ["x"]}, headers=headers).status_code == 401


def test_a_profile_token_in_the_header_needs_its_own_bearer(client: TestClient) -> None:
    a_headers, a_token = _signup(client, "a@example.com")
    b_headers, _ = _signup(client, "b@example.com")
    client.patch("/api/traveller", json={"preferences": ["food"]}, headers=a_headers)
    _seed_trending(client)

    for url in ("/api/traveller", "/api/wishlist", "/api/popular"):
        for bearer in ({}, {"Authorization": "Bearer not.a.token"}, b_headers):
            refused = client.get(url, headers={"X-Client-Token": a_token, **bearer})
            assert refused.status_code == 401, (url, bearer)
        own = client.get(url, headers={"X-Client-Token": a_token, **a_headers})
        assert own.status_code == 200, (url, own.text)
    assert client.get("/api/traveller", headers={"X-Client-Token": a_token, **a_headers}).json()["preferences"] == ["food"]


def test_a_signed_in_caller_who_still_sends_a_guest_header_gets_the_account(client: TestClient) -> None:
    headers, _ = _signup(client, "left-over@example.com")
    client.patch("/api/traveller", json={"preferences": ["account"]}, headers=headers)
    client.patch("/api/traveller", json={"preferences": ["leftover"]}, headers={"X-Client-Token": "guest-left"})

    profile = client.get("/api/traveller", headers={"X-Client-Token": "guest-left", **headers})

    assert profile.json()["preferences"] == ["account"]


def test_the_profile_echoes_only_the_identity_the_caller_presented(client: TestClient) -> None:
    """docs/API.md: no session response carries the token, the profile answers with the caller's own."""
    headers, profile_token = _signup(client, "echo@example.com")
    guest = {"X-Client-Token": "guest-echo"}

    assert client.get("/api/traveller", headers=guest).json()["client_token"] == "guest-echo"
    assert client.patch("/api/traveller", json={"preferences": ["a"]}, headers=guest).json()["profile"]["client_token"] == "guest-echo"
    assert client.get("/api/traveller", headers=headers).json()["client_token"] == profile_token
    created = client.post("/api/journeys", json={"client_token": "guest-echo"}).json()
    assert "guest-echo" not in str(created)


def test_the_frontend_never_puts_the_guest_token_in_a_url() -> None:
    """The page sends the token as a header; a URL is written to server and proxy logs."""
    offenders = [
        f"{path.relative_to(FRONTEND_SRC)}:{number}"
        for path in sorted(FRONTEND_SRC.rglob("*.ts*"))
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if re.search(r"client_token=|set\(\s*[\"']client_token[\"']", line)
    ]
    assert not offenders, f"the guest token is put in a URL at: {offenders}"
