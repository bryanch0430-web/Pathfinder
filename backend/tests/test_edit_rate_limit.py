"""The routes that write a plan version by hand share one per-client limit."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from plan_samples import make_plan

from app.config import Settings
from app.domain.models import Intent, Message
from app.interface.api import create_app

GUEST = {"X-Client-Token": "test"}


@pytest.fixture()
def trip(settings: Settings):
    limited = settings.model_copy(update={"rate_limit_edit_per_minute": 2})
    with TestClient(create_app(limited)) as client:
        session_id = client.post("/api/journeys", json={"client_token": "test"}).json()[
            "session"
        ]["id"]
        store = client.app.state.container.store
        plan = make_plan(days=2, with_tickets=False).model_copy(
            update={"session_id": session_id}
        )
        store.save_plan(plan, "First plan")
        message = Message(
            session_id=session_id,
            role="assistant",
            content="Held.",
            intent=Intent.MODIFY,
            meta={"proposal": {"changes": []}},
        )
        store.add_message(message)
        yield client, session_id, message.id


def _calls(session_id: str, message_id: str) -> dict[str, tuple[str, dict]]:
    return {
        "layout": (f"/api/journeys/{session_id}/layout", {"items": []}),
        "restore": (f"/api/journeys/{session_id}/itinerary/restore", {"version": 1}),
        "proposals": (
            f"/api/journeys/{session_id}/proposals/{message_id}",
            {"change_id": "nothing", "accept": False},
        ),
    }


@pytest.mark.parametrize("route", ["layout", "restore", "proposals"])
def test_a_route_that_writes_by_hand_answers_429_once_the_limit_is_used(
    trip, route: str
) -> None:
    client, session_id, message_id = trip
    path, body = _calls(session_id, message_id)[route]

    codes = [client.post(path, json=body, headers=GUEST).status_code for _ in range(3)]

    assert codes[-1] == 429 and 429 not in codes[:2], codes


def test_a_request_refused_as_not_yours_costs_nothing(trip) -> None:
    client, session_id, message_id = trip
    path, body = _calls(session_id, message_id)["layout"]

    strangers = [
        client.post(
            path, json=body, headers={"X-Client-Token": "someone-else"}
        ).status_code
        for _ in range(5)
    ]

    assert set(strangers) == {404}
    assert client.post(path, json=body, headers=GUEST).status_code == 200
