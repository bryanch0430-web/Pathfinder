import uuid

import pytest

from app.models.enums import MessageRole, MessageType, TripStatus
from app.models.trip import ConversationMessage, Trip
from app.core.auth import RequestIdentity
from app.schemas.trip import SendMessageRequest
from app.services.conversation import conversation_service
from conftest import TestingSessionLocal


def graph() -> dict:
    return {"version": 8,
            "nodes": [{"id": "origin", "name": "Osaka", "kind": "origin"},
                      {"id": "kyoto", "name": "Kyoto", "kind": "stop", "day_start": 1, "day_end": 3}],
            "edges": [{"id": "leg", "source": "origin", "target": "kyoto"}]}


@pytest.mark.asyncio
async def test_chat_confirmation_finalizes_both_trip_and_budget(client):
    headers = {"X-Guest-ID": str(uuid.uuid4())}
    created = (await client.post("/api/trips", headers=headers)).json()
    trip_id = created["trip_id"]
    assert (await client.post(f"/api/trips/{trip_id}/finalize", headers=headers)).status_code == 409
    async with TestingSessionLocal() as db:
        trip = await db.get(Trip, uuid.UUID(trip_id))
        trip.status = TripStatus.PLANNING
        trip.destination = "Kyoto"
        trip.duration_days = 3
        trip.planning_preferences = {"start_date": "2026-11-10"}
        db.add(ConversationMessage(session_id=uuid.UUID(created["session_id"]), role=MessageRole.ASSISTANT,
                                   message_type=MessageType.TEXT, content="Saved plan",
                                   payload={"kind": "ITINERARY_GRAPH", "graph": graph()}))
        await db.commit()
    assert (await client.post(f"/api/trips/{trip_id}/finalize", headers={"X-Guest-ID": str(uuid.uuid4())})).status_code == 403
    offer = (await client.post(f"/api/trips/{trip_id}/messages", headers=headers,
                               json={"content": "I'm satisfied, finalize and confirm it"})).json()
    assert offer["trip"]["status"] == "PLANNING"
    assert offer["assistant_message"]["payload"]["offer_finalize_trip"] is True
    assert "3 days" in offer["assistant_message"]["content"]
    assert "2026-11-10 to 2026-11-12" in offer["assistant_message"]["content"]
    confirmed = (await client.post(f"/api/trips/{trip_id}/messages", headers=headers,
                                   json={"content": "Yes, finalize"})).json()
    assert confirmed["trip"]["status"] == "COMPLETED"
    assert (await client.get(f"/api/trips/{trip_id}/document", headers=headers)).json()["is_final"] is True
    refused = (await client.post(f"/api/trips/{trip_id}/messages", headers=headers,
                                 json={"content": "Move Kyoto to day 2"})).json()
    assert "read-only" in refused["assistant_message"]["content"]
    async with TestingSessionLocal() as db:
        events = [event async for event in conversation_service.process_message_stream(
            db, uuid.UUID(trip_id), SendMessageRequest(content="Add a new stop"),
            RequestIdentity(guest_id=headers["X-Guest-ID"]))]
    assert any("read-only" in event for event in events)
    assert (await client.put(f"/api/trips/{trip_id}/budget/settings", headers=headers,
                             json={"currency": "INR", "target_amount": "500"})).status_code == 409
    assert (await client.post(f"/api/trips/{trip_id}/budget/lock", headers=headers,
                              json={"accept_suggestions": False})).status_code == 409
    assert (await client.post(f"/api/trips/{trip_id}/budget/unlock", headers=headers)).status_code == 409
    assert (await client.post(f"/api/trips/{trip_id}/finalize", headers=headers)).json()["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_guided_trip_must_finish_days_before_finalization(client):
    headers = {"X-Guest-ID": str(uuid.uuid4())}
    created = (await client.post("/api/trips", headers=headers)).json()
    trip_id = created["trip_id"]
    async with TestingSessionLocal() as db:
        trip = await db.get(Trip, uuid.UUID(trip_id))
        trip.status = TripStatus.PLANNING
        message = ConversationMessage(session_id=uuid.UUID(created["session_id"]), role=MessageRole.ASSISTANT,
                                      message_type=MessageType.TEXT, content="Guided plan",
                                      payload={"kind": "ITINERARY_GRAPH", "graph": graph(),
                                               "copilot": {"status": "building"}})
        db.add(message)
        await db.commit()
        message_id = message.id
    response = await client.post(f"/api/trips/{trip_id}/finalize", headers=headers)
    assert response.status_code == 409
    assert "Finish the guided" in response.json()["detail"]
    async with TestingSessionLocal() as db:
        message = await db.get(ConversationMessage, message_id)
        message.payload = {**message.payload, "copilot": {"status": "complete"}}
        await db.commit()
    assert (await client.post(f"/api/trips/{trip_id}/finalize", headers=headers)).json()["status"] == "COMPLETED"
