"""A traveller profile keeps at most 20 preferences of at most 80 characters, and says so when asked for more.

PATCH /api/traveller accepted up to 30 preferences of any length and then kept the first 20 without a
word, so the Settings page showed 22 tags and the profile held 20; a 300-character preference was kept
and later fed planning. The limits are now the request's own, and a request over them is refused.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.interface.api import create_app

GUEST = {"X-Client-Token": "guest-limits"}


@pytest.fixture()
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _stored(client: TestClient) -> list[str]:
    return client.get("/api/traveller", headers=GUEST).json()["preferences"]


def test_more_preferences_than_a_profile_keeps_are_refused_not_cut(client: TestClient) -> None:
    refused = client.patch("/api/traveller", json={"preferences": [f"tag {n}" for n in range(22)]}, headers=GUEST)
    assert refused.status_code == 422
    assert _stored(client) == []


def test_a_preference_longer_than_the_limit_is_refused(client: TestClient) -> None:
    refused = client.patch("/api/traveller", json={"preferences": ["x" * 300]}, headers=GUEST)
    assert refused.status_code == 422
    assert _stored(client) == []


def test_the_most_a_profile_keeps_is_kept_whole(client: TestClient) -> None:
    wanted = [f"{n:02d}" + "y" * 78 for n in range(20)]
    saved = client.patch("/api/traveller", json={"preferences": wanted}, headers=GUEST)
    assert saved.status_code == 200, saved.text
    assert saved.json()["profile"]["preferences"] == wanted
    assert _stored(client) == wanted
