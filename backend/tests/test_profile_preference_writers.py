"""Whatever writes a traveller's profile keeps to the limits that PATCH /api/traveller enforces.

The profile holds at most 20 preferences of at most 80 characters, newest last. Only the request was held
to that: the turns that teach a profile kept up to 30 entries of any length, feedback and the merge at
sign-in kept the FIRST 20 (so the newest were the ones lost), and the Settings page, which sends the whole
list back, then got 422 for a profile the system had written itself.
"""

from __future__ import annotations

import asyncio
import json
import random
import sqlite3
from contextlib import closing
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.understanding import understand
from app.config import Settings
from app.domain.models import PreferenceProfile, TripContext
from app.infrastructure.llm import LLMResult
from app.infrastructure.observability import NoopTracer
from app.interface.api import create_app
from scripts.seed_platform_demo import DESTINATIONS, _plan

GUEST = {"X-Client-Token": "guest-writers"}
MAX_ITEMS, MAX_LENGTH = 20, 80
#: a sentence a model may hand back as one "preference"
SENTENCE = "quiet cafes away from the tourist crowds with good coffee and a place to read for an hour or two"


@pytest.fixture()
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _learned(count: int) -> list[str]:
    return [f"learned {n:02d}" for n in range(count)]


def _teach(client: TestClient, preferences: list[str], owner: str = "guest-writers") -> None:
    """What a trip's turns teach the profile of the trip's owner."""
    client.app.state.container.memory.remember_preferences(owner, TripContext(session_id="s", preferences=preferences))


def _shown(client: TestClient, headers: dict[str, str] = GUEST) -> list[str]:
    response = client.get("/api/traveller", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["preferences"]


def _fits(preferences: list[str]) -> bool:
    return len(preferences) <= MAX_ITEMS and all(0 < len(value) <= MAX_LENGTH for value in preferences)


def test_a_profile_learned_from_turns_can_be_saved_back_from_the_settings_page(client: TestClient) -> None:
    learned = _learned(24) + [SENTENCE]  # 25 entries, the newest one is 97 characters

    _teach(client, learned)
    shown = _shown(client)

    assert _fits(shown), [len(shown), max(map(len, shown))]
    assert shown[:-1] == learned[5:-1], "the five oldest go, the newest stay"
    assert shown[-1] == SENTENCE[:MAX_LENGTH].rstrip()
    saved = client.patch("/api/traveller", json={"preferences": shown}, headers=GUEST)
    assert saved.status_code == 200, saved.text
    assert saved.json()["profile"]["preferences"] == shown


def test_a_profile_stored_before_the_limits_is_shown_within_them(client: TestClient, settings: Settings) -> None:
    legacy = _learned(30)  # what the turns used to keep: 30 entries, any length
    legacy[25] = "x" * 129
    row = {"client_token": "guest-writers", "preferences": legacy, "updated_at": "2026-10-01T00:00:00+00:00"}
    with closing(sqlite3.connect(settings.database_path)) as conn, conn:
        conn.execute(
            "INSERT INTO taste_profiles (client_token, data, updated_at) VALUES (?,?,?)",
            ("guest-writers", json.dumps(row), row["updated_at"]),
        )

    shown = _shown(client)

    assert shown == legacy[10:25] + ["x" * MAX_LENGTH] + legacy[26:]
    saved = client.patch("/api/traveller", json={"preferences": shown[1:]}, headers=GUEST)
    assert saved.status_code == 200, saved.text


def test_a_profile_never_holds_a_blank_or_a_repeat(client: TestClient) -> None:
    _teach(client, ["food", "  ", "museums", "food", " museums "])

    assert _shown(client) == ["food", "museums"]


def test_two_phrases_that_are_one_after_the_cut_are_kept_once() -> None:
    profile = PreferenceProfile(client_token="t", preferences=["a" * 85, "a" * 90, "b"])

    assert profile.preferences == ["a" * MAX_LENGTH, "b"]


def _rated_trip(client: TestClient, headers: dict[str, str]) -> str:
    created = client.post("/api/journeys", json={}, headers=headers)
    assert created.status_code == 200, created.text
    session_id = created.json()["session"]["id"]
    place = next(item for item in DESTINATIONS if item["name"] == "Kyoto")
    plan = _plan(random.Random(1), place, 1).model_copy(update={"session_id": session_id})
    client.app.state.container.store.save_plan(plan, "test plan")
    return session_id


def test_feedback_adds_what_it_derived_to_a_full_profile_and_drops_the_oldest(client: TestClient) -> None:
    _teach(client, _learned(MAX_ITEMS))
    session_id = _rated_trip(client, GUEST)

    rated = client.post(f"/api/journeys/{session_id}/rating", json={"useful": True, "stars": 5}, headers=GUEST)

    assert rated.status_code == 200, rated.text
    added = rated.json()["preferences_added"]
    shown = _shown(client)
    assert added, "the plan has meals and sights, so something is derived from it"
    assert _fits(shown)
    assert shown[-len(added) :] == added, "what the answer says was added is what the profile now ends with"
    assert shown[: -len(added)] == _learned(MAX_ITEMS)[len(added) :]


def test_the_merge_at_sign_in_keeps_the_newest_of_a_long_guest_profile(client: TestClient) -> None:
    learned = _learned(25)
    _teach(client, learned, owner="guest-merge")

    signed_up = client.post(
        "/api/account/signup",
        json={"email": "writers@example.com", "password": "correct-horse", "client_token": "guest-merge"},
    )
    assert signed_up.status_code == 200, signed_up.text
    account = {"Authorization": f"Bearer {signed_up.json()['token']}"}

    assert _shown(client, account) == learned[-MAX_ITEMS:]


def test_the_merge_at_sign_in_puts_the_guests_phrases_after_the_accounts_and_keeps_the_newest(client: TestClient) -> None:
    signed_up = client.post(
        "/api/account/signup", json={"email": "again@example.com", "password": "correct-horse"}
    ).json()
    account = {"Authorization": f"Bearer {signed_up['token']}"}
    saved = client.patch("/api/traveller", json={"preferences": [f"account {n:02d}" for n in range(15)]}, headers=account)
    assert saved.status_code == 200, saved.text
    _teach(client, [f"guest {n:02d}" for n in range(10)], owner="guest-second")

    signed_in = client.post(
        "/api/account/signin",
        json={"email": "again@example.com", "password": "correct-horse", "client_token": "guest-second"},
    )
    assert signed_in.status_code == 200, signed_in.text

    assert _shown(client, account) == [f"account {n:02d}" for n in range(5, 15)] + [f"guest {n:02d}" for n in range(10)]


class _Says:
    """A writer model that answers the understanding prompt with the given preferences."""

    available = True
    name = "says"

    def __init__(self, preferences: list[str]) -> None:
        self.preferences = preferences

    async def complete(self, *, system, user, purpose, json_mode=True, temperature=0.2, max_tokens=4096):
        return LLMResult(text=json.dumps({"preferences": self.preferences}), model="says")


def test_a_preference_the_model_hands_back_is_cut_where_it_is_read(settings: Settings) -> None:
    gateway = LLMGateway(_Says([SENTENCE, "ramen"]), PromptLibrary(settings), NoopTracer(), settings)

    context = asyncio.run(understand("Plan a trip", TripContext(session_id="s"), gateway, today=date(2026, 11, 3)))

    assert context.preferences == [SENTENCE[:MAX_LENGTH].rstrip(), "ramen"]


def test_two_long_phrases_the_model_hands_back_that_are_one_after_the_cut_are_kept_once(settings: Settings) -> None:
    gateway = LLMGateway(
        _Says([SENTENCE, SENTENCE + " and then some more"]), PromptLibrary(settings), NoopTracer(), settings
    )

    context = asyncio.run(understand("Plan a trip", TripContext(session_id="s"), gateway, today=date(2026, 11, 3)))

    assert context.preferences == [SENTENCE[:MAX_LENGTH].rstrip()]
