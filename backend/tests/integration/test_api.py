"""HTTP + WebSocket API, through FastAPI's TestClient (runs the app lifespan)."""

from __future__ import annotations

import json
from collections.abc import Iterator

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
