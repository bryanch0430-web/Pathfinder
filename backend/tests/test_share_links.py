"""Share links expire after Settings.share_ttl_days and the session owner can revoke them."""

from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.infrastructure.store import BASELINE_SCHEMA, MIGRATIONS, SQLiteStore
from app.interface.api import create_app
from scripts.seed_platform_demo import DESTINATIONS, _plan

PASSWORD = "correct-horse"


@pytest.fixture()
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _store(client: TestClient) -> SQLiteStore:
    return client.app.state.container.store


def _signup(client: TestClient, email: str) -> dict[str, str]:
    response = client.post("/api/account/signup", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _session_with_plan(client: TestClient, headers: dict[str, str] | None = None) -> str:
    created = client.post("/api/journeys", json={"client_token": "guest-share"}, headers=headers or {})
    assert created.status_code == 200, created.text
    session_id = created.json()["session"]["id"]
    place = next(item for item in DESTINATIONS if item["name"] == "Kyoto")
    plan = _plan(random.Random(7), place, 7).model_copy(update={"session_id": session_id})
    _store(client).save_plan(plan, "test plan")
    return session_id


def _share(client: TestClient, session_id: str, headers: dict[str, str] | None = None) -> dict:
    response = client.post(f"/api/journeys/{session_id}/share", headers=headers or {})
    assert response.status_code == 200, response.text
    return response.json()


def _not_found(client: TestClient) -> tuple[tuple[int, str], tuple[int, str]]:
    """What both public routes answer for a slug that never existed."""
    json_route = client.get("/api/public/trip_neverexisted")
    page_route = client.get("/s/trip_neverexisted")
    assert json_route.status_code == 404 and page_route.status_code == 404
    return (json_route.status_code, json_route.text), (page_route.status_code, page_route.text)


def _public(client: TestClient, slug: str) -> tuple[tuple[int, str], tuple[int, str]]:
    json_route = client.get(f"/api/public/{slug}")
    page_route = client.get(f"/s/{slug}")
    return (json_route.status_code, json_route.text), (page_route.status_code, page_route.text)


def _expire(client: TestClient, slug: str) -> None:
    with sqlite3.connect(_store(client).path) as conn:
        conn.execute(
            "UPDATE share_links SET expires_at=? WHERE slug=?",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(timespec="seconds"), slug),
        )
    conn.close()


def test_share_response_carries_its_expiry(client: TestClient) -> None:
    session_id = _session_with_plan(client)
    before = datetime.now(timezone.utc)
    body = _share(client, session_id)

    expires_at = datetime.fromisoformat(body["expires_at"])
    assert expires_at.utcoffset() == timedelta(0)
    assert before + timedelta(days=30) - timedelta(seconds=5) <= expires_at
    assert expires_at <= datetime.now(timezone.utc) + timedelta(days=30)
    assert body["url"] == f"/s/{body['slug']}"
    json_route, page_route = _public(client, body["slug"])
    assert json_route[0] == 200 and page_route[0] == 200


def test_share_ttl_comes_from_settings(settings: Settings) -> None:
    with TestClient(create_app(settings.model_copy(update={"share_ttl_days": 7}))) as scoped:
        body = _share(scoped, _session_with_plan(scoped))
    remaining = datetime.fromisoformat(body["expires_at"]) - datetime.now(timezone.utc)
    assert timedelta(days=6, hours=23) < remaining <= timedelta(days=7)


def test_expired_link_answers_like_an_unknown_one_on_both_routes(client: TestClient) -> None:
    slug = _share(client, _session_with_plan(client))["slug"]
    _expire(client, slug)

    assert _public(client, slug) == _not_found(client)


def test_owner_revokes_every_link_of_the_session(client: TestClient) -> None:
    headers = _signup(client, "owner@example.com")
    session_id = _session_with_plan(client, headers)
    first = _share(client, session_id, headers)["slug"]
    second = _share(client, session_id, headers)["slug"]
    other_session = _session_with_plan(client, headers)
    other = _share(client, other_session, headers)["slug"]

    revoked = client.delete(f"/api/journeys/{session_id}/share", headers=headers)

    assert revoked.status_code == 200, revoked.text
    assert revoked.json() == {"revoked": 2}
    assert _public(client, first) == _not_found(client)
    assert _public(client, second) == _not_found(client)
    # Another session's link is untouched, and revoking again finds nothing.
    assert _public(client, other)[0][0] == 200
    assert client.delete(f"/api/journeys/{session_id}/share", headers=headers).json() == {"revoked": 0}


def test_deleting_a_session_turns_off_its_links(client: TestClient) -> None:
    headers = _signup(client, "deleter@example.com")
    session_id = _session_with_plan(client, headers)
    slug = _share(client, session_id, headers)["slug"]

    deleted = client.delete(f"/api/journeys/{session_id}", headers=headers)

    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"]["shares"] == 1
    assert _public(client, slug) == _not_found(client)


def test_a_different_identity_cannot_revoke(client: TestClient) -> None:
    owner = _signup(client, "owner@example.com")
    stranger = _signup(client, "stranger@example.com")
    session_id = _session_with_plan(client, owner)
    slug = _share(client, session_id, owner)["slug"]
    # The status require_session gives any other identity on an owned session.
    refused = client.get(f"/api/journeys/{session_id}", headers=stranger)
    assert refused.status_code == 404

    by_stranger = client.delete(f"/api/journeys/{session_id}/share", headers=stranger)
    anonymous = client.delete(f"/api/journeys/{session_id}/share")

    assert by_stranger.status_code == refused.status_code
    assert by_stranger.json() == refused.json()
    assert anonymous.status_code == refused.status_code
    assert _public(client, slug)[0][0] == 200
    assert client.delete("/api/journeys/ses_unknown/share", headers=owner).status_code == 404


def test_links_from_before_expiry_get_a_full_lifetime_on_upgrade(tmp_path: Path) -> None:
    path = tmp_path / "old-shares.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(BASELINE_SCHEMA)
        conn.execute(
            "INSERT INTO share_links (slug, session_id, trip_id, version, data, created_at) VALUES (?,?,?,?,?,?)",
            ("trip_oldlink", "ses_old", "trip_old", 1, '{"summary": "old"}', "2025-01-01T00:00:00+00:00"),
        )
        conn.execute("PRAGMA user_version = 1")
    conn.close()

    store = SQLiteStore(path, share_ttl_days=10)

    assert store.schema_version == len(MIGRATIONS)
    with sqlite3.connect(path) as conn:
        (expires_at,) = conn.execute("SELECT expires_at FROM share_links WHERE slug='trip_oldlink'").fetchone()
    conn.close()
    remaining = datetime.fromisoformat(expires_at) - datetime.now(timezone.utc)
    assert timedelta(days=9, hours=23) < remaining <= timedelta(days=10)
    assert store.get_share("trip_oldlink") == {"summary": "old"}
