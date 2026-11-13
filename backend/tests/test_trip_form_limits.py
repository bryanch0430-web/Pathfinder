"""A trip's form keeps at most 50 preferences and 50 constraints of at most 200 characters, and its other lines
(destination, origin, hotel style, pace, currency, language) at most 120 characters.

Nothing bounded the form: one PATCH of 30,000 preferences (a 250 KiB request, answered 200) turned every later
message of the trip into seconds of CPU on the thread pool that serves everyone, because each message copies the
trip's list into the owner's profile with a loop and a validator that are quadratic in it. A 3 MB line was kept
as well and then shipped in every prompt of the trip. The limits are the request's own (a request over them is
refused, as PATCH /api/traveller does for a profile); what the system itself writes (the understanding step) and a
form stored before the limits are cut to them, newest kept, so the page can always send back what it was shown.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
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

GUEST = {"X-Client-Token": "guest-form-limits"}
LINES, LINE, FIELD = 50, 200, 120
#: CPU seconds. The work takes milliseconds; the quadratic loops took seconds at these sizes.
BUDGET = 2.0


@pytest.fixture()
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _trip(client: TestClient) -> str:
    created = client.post("/api/journeys", json={}, headers=GUEST)
    assert created.status_code == 200, created.text
    return created.json()["session"]["id"]


def _stored(client: TestClient, session_id: str) -> dict:
    opened = client.get(f"/api/journeys/{session_id}", headers=GUEST)
    assert opened.status_code == 200, opened.text
    return opened.json()["context"]


def _patch(client: TestClient, session_id: str, body: dict):
    return client.patch(f"/api/journeys/{session_id}/state", json=body, headers=GUEST)


@pytest.mark.parametrize("field", ["preferences", "hard_constraints"])
def test_more_lines_than_a_form_keeps_are_refused_not_cut(client: TestClient, field: str) -> None:
    session_id = _trip(client)

    refused = _patch(client, session_id, {field: [f"line {n}" for n in range(LINES + 1)]})

    assert refused.status_code == 422, refused.text
    assert _stored(client, session_id)[field] == []


@pytest.mark.parametrize("field", ["preferences", "hard_constraints"])
def test_a_line_longer_than_the_limit_is_refused(client: TestClient, field: str) -> None:
    session_id = _trip(client)

    refused = _patch(client, session_id, {field: ["x" * (LINE + 1)]})

    assert refused.status_code == 422, refused.text
    assert _stored(client, session_id)[field] == []


@pytest.mark.parametrize("field", ["destination", "origin", "hotel_style", "pace", "currency", "language"])
def test_a_text_field_longer_than_the_limit_is_refused(client: TestClient, field: str) -> None:
    session_id = _trip(client)
    before = _stored(client, session_id)[field]

    refused = _patch(client, session_id, {field: "y" * (FIELD + 1)})

    assert refused.status_code == 422, refused.text
    assert _stored(client, session_id)[field] == before


def test_a_request_that_would_have_hung_the_service_is_refused_at_once(client: TestClient) -> None:
    session_id = _trip(client)
    started = time.process_time()

    refused = _patch(client, session_id, {"preferences": [f"p{n}" for n in range(30_000)]})

    assert refused.status_code == 422, refused.text
    assert time.process_time() - started < BUDGET
    assert _stored(client, session_id)["preferences"] == []


def test_the_most_a_form_keeps_is_kept_whole_and_can_be_sent_back(client: TestClient) -> None:
    session_id = _trip(client)
    wanted = {
        "preferences": [f"{n:02d}" + "p" * (LINE - 2) for n in range(LINES)],
        "hard_constraints": [f"{n:02d}" + "c" * (LINE - 2) for n in range(LINES)],
        "destination": "d" * FIELD,
        "origin": "o" * FIELD,
        "hotel_style": "h" * FIELD,
        # The pace is one of three words since the traveller profile (a longer text is read as "balanced"),
        # so it no longer carries a maximum-length text; the refusal of an over-long pace is tested above.
        "pace": "relaxed",
    }

    saved = _patch(client, session_id, wanted)

    assert saved.status_code == 200, saved.text
    shown = _stored(client, session_id)
    assert {key: shown[key] for key in wanted} == wanted
    again = _patch(client, session_id, {key: shown[key] for key in wanted})
    assert again.status_code == 200, again.text


def test_a_form_stored_before_the_limits_is_read_within_them(client: TestClient, settings: Settings) -> None:
    session_id = _trip(client)
    legacy = {
        "session_id": session_id,
        "destination": "Kyoto" + " " * 5 + "z" * 4_000,
        "preferences": [f"p{n}" for n in range(100_000)],
        "hard_constraints": ["c" * 3_000_000] + [f"constraint {n}" for n in range(LINES + 10)],
    }
    with closing(sqlite3.connect(settings.database_path)) as conn, conn:
        conn.execute("UPDATE journey_state SET data=? WHERE session_id=?", (json.dumps(legacy), session_id))
    started = time.process_time()

    shown = _stored(client, session_id)

    assert time.process_time() - started < BUDGET
    assert shown["preferences"] == [f"p{n}" for n in range(100_000 - LINES, 100_000)], "the newest are kept"
    assert shown["hard_constraints"] == [f"constraint {n}" for n in range(10, LINES + 10)]
    assert len(shown["destination"]) <= FIELD
    assert _patch(client, session_id, {"preferences": shown["preferences"]}).status_code == 200


def test_every_writer_of_the_form_is_held_to_the_limits_by_the_store(client: TestClient) -> None:
    """The understanding step appends to the lists in place, so what is saved is cut where all writers meet."""
    session_id = _trip(client)
    store = client.app.state.container.store
    context = store.get_context(session_id)
    assert context is not None
    context.preferences.extend(f"learned {n}" for n in range(LINES + 25))
    context.hard_constraints.append("w" * 5_000)
    context.destination = "v" * 5_000

    store.save_context(context)

    stored = store.get_context(session_id)
    assert stored is not None
    assert stored.preferences == [f"learned {n}" for n in range(25, LINES + 25)]
    assert stored.hard_constraints == ["w" * LINE]
    assert stored.destination == "v" * FIELD
    assert context.model_dump() == stored.model_dump(), "the object that was saved says what is stored"


class _Says:
    """A writer model that answers the understanding prompt with the given lines."""

    available = True
    name = "says"

    def __init__(self, **answer) -> None:
        self.answer = answer

    async def complete(self, *, system, user, purpose, json_mode=True, temperature=0.2, max_tokens=4096):
        return LLMResult(text=json.dumps(self.answer), model="says")


def test_what_a_model_hands_back_to_the_understanding_step_is_cut_where_it_is_saved(
    client: TestClient, settings: Settings
) -> None:
    session_id = _trip(client)
    container = client.app.state.container
    gateway = LLMGateway(
        _Says(destination="k" * 5_000, hard_constraints=["m" * 5_000, "short one must stay"]),
        PromptLibrary(settings),
        NoopTracer(),
        settings,
    )
    as_read = container.store.get_context(session_id)
    assert as_read is not None
    changed = asyncio.run(understand("Plan a trip", as_read, gateway, today=date(2026, 11, 3)))

    saved = container.turn_service._save_changes(as_read, changed)

    stored = container.store.get_context(session_id)
    assert stored is not None
    assert stored.destination == "k" * FIELD
    assert stored.hard_constraints == ["m" * LINE, "short one must stay"]
    assert saved.model_dump() == stored.model_dump()


def test_the_model_cuts_what_is_built_from_it() -> None:
    context = TripContext(
        session_id="s", preferences=[f"p{n}" for n in range(LINES + 5)], hard_constraints=["h" * (LINE + 30)]
    )

    assert context.preferences == [f"p{n}" for n in range(5, LINES + 5)]
    assert context.hard_constraints == ["h" * LINE]


def test_a_profile_is_fitted_in_time_in_proportion_to_what_it_is_given() -> None:
    started = time.process_time()

    profile = PreferenceProfile(client_token="t", preferences=[f"p{n}" for n in range(40_000)])

    assert time.process_time() - started < BUDGET
    assert profile.preferences == [f"p{n}" for n in range(40_000 - 20, 40_000)]


def test_what_a_trip_teaches_the_profile_takes_time_in_proportion_to_the_trips_list(client: TestClient) -> None:
    """A form that was built without the limits (not through the model) still must not stall the thread pool."""
    memory = client.app.state.container.memory
    huge = TripContext.model_construct(session_id="s", preferences=[f"p{n}" for n in range(40_000)])
    started = time.process_time()

    profile = memory.remember_preferences("guest-form-limits", huge)

    assert time.process_time() - started < BUDGET
    assert profile is not None
    assert profile.preferences == [f"p{n}" for n in range(40_000 - 20, 40_000)]
