"""Signing up or in moves the guest's preferences to the account; it does not copy them.

The page keeps the guest token when someone signs out. A copy left behind meant that "Clear memory" as the
account removed the account's preferences and the same browser, signed out, still showed the guest's.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.models import PreferenceProfile
from app.infrastructure.store import SQLiteStore
from app.interface.api import create_app

PASSWORD = "correct-horse"
GUEST = {"X-Client-Token": "guest-browser"}


def _store(tmp_path: Path) -> SQLiteStore:
    return SQLiteStore(tmp_path / "preferences.db")


def test_merging_moves_the_guest_profile_to_the_account(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_preferences(PreferenceProfile(client_token="guest-a", preferences=["food", "nature"]))
    store.upsert_preferences(PreferenceProfile(client_token="user:u1", preferences=["museums", "food"]))

    merged = store.merge_preferences("guest-a", "user:u1")

    assert merged.preferences == ["museums", "food", "nature"]
    assert store.get_preferences("user:u1").preferences == ["museums", "food", "nature"]
    assert store.get_preferences("guest-a") is None


def test_merging_a_profile_into_itself_keeps_it(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_preferences(PreferenceProfile(client_token="guest-a", preferences=["food"]))

    store.merge_preferences("guest-a", "guest-a")

    assert store.get_preferences("guest-a").preferences == ["food"]


def test_merging_when_the_guest_has_no_profile_leaves_the_account_as_it_is(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.upsert_preferences(PreferenceProfile(client_token="user:u1", preferences=["museums"]))

    store.merge_preferences("guest-none", "user:u1")

    assert store.get_preferences("user:u1").preferences == ["museums"]
    assert store.get_preferences("guest-none") is None


def _client(settings: Settings):
    return TestClient(create_app(settings))


def _bearer(response) -> dict[str, str]:
    return {"Authorization": f"Bearer {response.json()['token']}"}


def test_clearing_memory_as_the_account_leaves_nothing_on_the_guest_token_of_the_same_browser(
    settings: Settings,
) -> None:
    with _client(settings) as client:
        client.post("/api/journeys", json={"client_token": "guest-browser"})
        client.patch("/api/traveller", json={"preferences": ["food", "nature"]}, headers=GUEST)
        signed_up = client.post(
            "/api/account/signup",
            json={"email": "mover@example.com", "password": PASSWORD, "client_token": "guest-browser"},
        )
        account = _bearer(signed_up)

        as_account = client.get("/api/traveller", headers=account).json()
        client.post("/api/recall/clear", headers=account)
        cleared_account = client.get("/api/traveller", headers=account).json()
        signed_out = client.get("/api/traveller", headers=GUEST).json()

    assert as_account["preferences"] == ["food", "nature"]
    assert cleared_account["preferences"] == []
    assert signed_out["preferences"] == []


def test_signing_in_to_an_account_moves_the_preferences_of_the_guest_token_too(settings: Settings) -> None:
    with _client(settings) as client:
        first = client.post("/api/account/signup", json={"email": "again@example.com", "password": PASSWORD})
        client.patch("/api/traveller", json={"preferences": ["museums"]}, headers=_bearer(first))
        client.patch("/api/traveller", json={"preferences": ["food"]}, headers=GUEST)

        signed_in = client.post(
            "/api/account/signin",
            json={"email": "again@example.com", "password": PASSWORD, "client_token": "guest-browser"},
        )

        account = client.get("/api/traveller", headers=_bearer(signed_in)).json()
        guest = client.get("/api/traveller", headers=GUEST).json()

    assert account["preferences"] == ["museums", "food"]
    assert guest["preferences"] == []
