"""HTTP + WebSocket API, through FastAPI's TestClient (runs the app lifespan)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.api.main import create_app
from backend.container import build_container
from backend.tests.integration.conftest import make_settings

CONTEXT = {
    "destination": "Kyoto",
    "origin": "Tokyo",
    "start_date": "2026-04-10",
    "end_date": "2026-04-12",
    "party_size": 2,
    "budget": {"amount": 300000, "currency": "JPY"},
}


@pytest.fixture
def client() -> Iterator[TestClient]:
    app = create_app(lambda: build_container(make_settings(), sinks=[]))
    with TestClient(app) as test_client:
        yield test_client


def test_api_session_plan_confirm_feedback_flow(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"
    created = client.post("/api/sessions", json={"context": {}})
    assert created.status_code == 201
    sid = created.json()["session_id"]

    updated = client.put(f"/api/sessions/{sid}/context", json={"context": CONTEXT})
    assert updated.status_code == 200 and updated.json()["context"]["destination"] == "Kyoto"

    turn = client.post(f"/api/sessions/{sid}/messages", json={"message": "Plan my trip to Kyoto please"})
    assert turn.status_code == 200
    body = turn.json()
    assert body["route"] == "plan" and body["plan"]["destination"] == "Kyoto"

    item_id = body["plan"]["days"][0]["items"][0]["item_id"]
    confirmed = client.post(f"/api/sessions/{sid}/plan/confirm", json={"item_ids": [item_id]})
    assert confirmed.status_code == 200
    assert confirmed.json()["days"][0]["items"][0]["confirmed"] is True
    assert client.post(f"/api/sessions/{sid}/plan/confirm", json={"item_ids": ["nope"]}).status_code == 422

    feedback = client.post(f"/api/sessions/{sid}/plan/feedback", json={"useful": True, "rating": 5})
    assert feedback.status_code == 200 and feedback.json() == {"queued": True}

    view = client.get(f"/api/sessions/{sid}").json()
    assert len(view["history"]) == 2 and view["plan"]["version"] == 1


def test_api_errors(client: TestClient) -> None:
    assert client.get("/api/sessions/missing").status_code == 404
    sid = client.post("/api/sessions", json={"context": {}}).json()["session_id"]
    assert client.post(f"/api/sessions/{sid}/plan/feedback", json={"useful": True, "rating": 5}).status_code == 409
    assert client.post(f"/api/sessions/{sid}/messages", json={"message": ""}).status_code == 422


def test_api_websocket_streams_events_and_result(client: TestClient) -> None:
    sid = client.post("/api/sessions", json={"context": CONTEXT}).json()["session_id"]
    with client.websocket_connect(f"/api/sessions/{sid}/stream") as ws:
        ws.send_text(json.dumps({"message": "Plan my trip to Kyoto please"}))
        events = []
        while True:
            event = json.loads(ws.receive_text())
            events.append(event)
            if event["type"] == "done":
                break
    kinds = [e["type"] for e in events]
    assert kinds[0] == "route"
    assert kinds.count("agent_started") == 4 and kinds.count("agent_finished") == 4
    assert "plan" in kinds
    assert events[-1]["result"]["plan"]["destination"] == "Kyoto"


def test_openapi_includes_turn_event_schema(client: TestClient) -> None:
    schemas = client.get("/openapi.json").json()["components"]["schemas"]
    assert {"TripPlan", "TurnResult", "TurnEvent", "SessionView"} <= set(schemas)


# ---- plan workspace: chat focus ---------------------------------------------------------------


def _planned(client: TestClient) -> tuple[str, dict[str, Any]]:
    sid = client.post("/api/sessions", json={"context": CONTEXT}).json()["session_id"]
    turn = client.post(f"/api/sessions/{sid}/messages", json={"message": "Plan my trip to Kyoto please"})
    assert turn.status_code == 200
    plan: dict[str, Any] = turn.json()["plan"]
    return sid, plan


def _until_done(ws: Any) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    while True:
        event = json.loads(ws.receive_text())
        events.append(event)
        if event["type"] == "done":
            return events


def test_api_chat_focus_needs_a_plan_and_a_part_of_it(client: TestClient) -> None:
    sid = client.post("/api/sessions", json={"context": CONTEXT}).json()["session_id"]
    focus = {"kind": "item", "id": "kyoto-kiyomizu-dera@2026-04-10"}
    no_plan = client.post(f"/api/sessions/{sid}/messages", json={"message": "Swap this for a museum", "focus": focus})
    assert no_plan.status_code == 409 and no_plan.json() == {"detail": "session has no plan"}

    client.post(f"/api/sessions/{sid}/messages", json={"message": "Plan my trip to Kyoto please"})
    unknown = client.post(
        f"/api/sessions/{sid}/messages",
        json={"message": "Swap this for a museum", "focus": {"kind": "item", "id": "nope"}},
    )
    assert unknown.status_code == 422 and "nope" in unknown.json()["detail"]
    bad_kind = client.post(
        f"/api/sessions/{sid}/messages",
        json={"message": "Swap this for a museum", "focus": {"kind": "restaurant", "id": "x"}},
    )
    assert bad_kind.status_code == 422
    assert len(client.get(f"/api/sessions/{sid}").json()["history"]) == 2  # rejected turns are not recorded


def test_api_chat_accepts_a_focus_on_the_current_plan(client: TestClient) -> None:
    sid, plan = _planned(client)
    focus = {"kind": "item", "id": plan["days"][0]["items"][1]["item_id"]}
    turn = client.post(
        f"/api/sessions/{sid}/messages", json={"message": "What time do we get there?", "focus": focus}
    )
    assert turn.status_code == 200 and turn.json()["route"] == "ask"
    plain = client.post(f"/api/sessions/{sid}/messages", json={"message": "What time do we get there?", "focus": None})
    assert plain.status_code == 200


def test_api_websocket_rejects_a_stale_focus_and_keeps_the_socket_open(client: TestClient) -> None:
    sid, plan = _planned(client)
    item_id = plan["days"][0]["items"][1]["item_id"]
    with client.websocket_connect(f"/api/sessions/{sid}/stream") as ws:
        ws.send_text(
            json.dumps({"message": "Swap this for a museum", "focus": {"kind": "item", "id": "gone@2026-04-10"}})
        )
        error = json.loads(ws.receive_text())
        assert error["type"] == "error" and error["trace_id"] == ""
        assert "gone@2026-04-10" in error["message"]

        ws.send_text(json.dumps({"message": "What time do we get there?", "focus": {"kind": "item", "id": item_id}}))
        events = _until_done(ws)
    assert events[-1]["result"]["route"] == "ask"


def test_api_post_and_websocket_carry_the_focus_into_the_turn(client: TestClient) -> None:
    sid, plan = _planned(client)
    first, second = plan["days"][0]["items"][:2]
    turn = client.post(
        f"/api/sessions/{sid}/messages",
        json={"message": "Swap this for a museum", "focus": {"kind": "item", "id": first["item_id"]}},
    )
    body = turn.json()
    assert body["route"] == "modify" and body["agents_run"] == ["attraction"]
    items = body["plan"]["days"][0]["items"]
    assert items[0]["item_id"] != first["item_id"] and items[1] == second  # only the focused stop changed

    with client.websocket_connect(f"/api/sessions/{sid}/stream") as ws:
        ws.send_text(
            json.dumps({"message": "Swap this for an aquarium", "focus": {"kind": "item", "id": second["item_id"]}})
        )
        result = _until_done(ws)[-1]["result"]
    assert result["agents_run"] == ["attraction"]
    new_items = result["plan"]["days"][0]["items"]
    assert new_items[0] == items[0] and new_items[1]["item_id"] != second["item_id"]
