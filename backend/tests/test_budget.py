import uuid

import pytest
from httpx import AsyncClient

from app.models.enums import MessageRole, MessageType
from app.models.trip import ConversationMessage
from app.models.trip import Trip
from app.models.enums import TripStatus
from app.core.auth import RequestIdentity
from app.schemas.trip import SendMessageRequest
from app.services.conversation import conversation_service
from app.services.budget import _desired_rows
from conftest import TestingSessionLocal


def itinerary(stops: list[str]) -> dict:
    nodes = [{"id": "origin", "name": "Osaka", "kind": "origin"}]
    nodes += [{"id": f"stop-{index}", "name": name, "kind": "stop"}
              for index, name in enumerate(stops)]
    edges = [{"id": f"leg-{index}", "source": nodes[index]["id"],
              "target": nodes[index + 1]["id"], "cost": "¥500 fare" if index == 0 else None}
             for index in range(len(nodes) - 1)]
    return {"version": 8, "nodes": nodes, "edges": edges}


def test_transfer_hubs_and_day_visits_do_not_create_hotel_costs():
    graph = itinerary(["Kyoto", "Kyoto Station", "Fushimi Inari"])
    graph["nodes"][1].update(day_start=1, day_end=3)
    graph["nodes"][2].update(day_start=1, day_end=3)
    graph["nodes"][3].update(day_start=3, day_end=3)
    labels = {row["label"] for row in _desired_rows(graph).values()}
    assert "Stay in Kyoto" in labels
    assert "Stay in Kyoto Station" not in labels
    assert "Stay in Fushimi Inari" not in labels
    assert "Activities in Fushimi Inari" in labels


@pytest.mark.asyncio
async def test_budget_persists_recalculates_and_reconciles_changed_route(client: AsyncClient):
    guest = str(uuid.uuid4())
    headers = {"X-Guest-ID": guest}
    created = (await client.post("/api/trips", headers=headers)).json()
    trip_id = created["trip_id"]
    async with TestingSessionLocal() as db:
        message = ConversationMessage(
            session_id=uuid.UUID(created["session_id"]), role=MessageRole.ASSISTANT,
            message_type=MessageType.TEXT, content="Trip draft",
            payload={"kind": "ITINERARY_GRAPH", "graph": itinerary(["Kyoto", "Nara"])},
        )
        db.add(message)
        await db.commit()
        message_id = message.id

    initial = (await client.get(f"/api/trips/{trip_id}/budget", headers=headers)).json()
    assert initial["priced_total"] == "0.00"
    assert initial["unpriced_count"] == 8  # Base city, day visit, and two travel legs.
    assert initial["target_amount"] is None
    travel = next(item for item in initial["items"] if item["label"] == "Travel Osaka → Kyoto")
    assert travel["quote_text"] == "¥500 fare"
    assert travel["unit_amount"] is None  # A draft quote is not counted as a verified price.

    settings = await client.put(f"/api/trips/{trip_id}/budget/settings", headers=headers,
                                json={"currency": "JPY", "target_amount": "10000"})
    assert settings.status_code == 200
    stay = next(item for item in settings.json()["items"] if item["label"] == "Stay in Kyoto")
    saved = await client.put(f"/api/trips/{trip_id}/budget/items/{stay['id']}", headers=headers,
                             json={"label": stay["label"], "category": "stay", "place_name": "Kyoto",
                                   "quantity": "2", "unit_amount": "3000", "is_included": True})
    assert saved.status_code == 200
    assert saved.json()["priced_total"] == "6000.00"
    assert saved.json()["remaining"] == "4000.00"
    assert saved.json()["category_totals"]["stay"] == "6000.00"
    nara = next(item for item in saved.json()["items"] if item["label"] == "Activities in Nara")
    priced_visit = await client.put(f"/api/trips/{trip_id}/budget/items/{nara['id']}", headers=headers,
                                    json={"label": nara["label"], "category": "activities", "place_name": "Nara",
                                          "quantity": "1", "unit_amount": "200", "is_included": True})
    assert priced_visit.json()["priced_total"] == "6200.00"

    reopened = (await client.get(f"/api/trips/{trip_id}/budget", headers=headers)).json()
    assert reopened["priced_total"] == "6200.00"

    async with TestingSessionLocal() as db:
        message = await db.get(ConversationMessage, message_id)
        message.payload = {"kind": "ITINERARY_GRAPH", "graph": itinerary(["Kyoto", "Kobe"])}
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(message, "payload")
        await db.commit()
    changed = (await client.get(f"/api/trips/{trip_id}/budget", headers=headers)).json()
    assert changed["priced_total"] == "6000.00"  # Matching Kyoto row keeps traveler amount.
    assert changed["review_count"] == 1  # Only the priced Nara row needs review; blank rows are removed.
    assert any(item["label"] == "Activities in Kobe" and item["is_current"] for item in changed["items"])
    assert any(item["label"] == "Activities in Nara" and not item["is_current"] for item in changed["items"])


@pytest.mark.asyncio
async def test_budget_manual_items_validation_and_ownership(client: AsyncClient):
    guest = str(uuid.uuid4())
    headers = {"X-Guest-ID": guest}
    trip_id = (await client.post("/api/trips", headers=headers)).json()["trip_id"]
    url = f"/api/trips/{trip_id}/budget"
    assert (await client.get(url, headers={"X-Guest-ID": str(uuid.uuid4())})).status_code == 403
    assert (await client.post(f"{url}/items", headers={"X-Guest-ID": str(uuid.uuid4())}, json={
        "label": "Unauthorized", "category": "other", "quantity": "1",
    })).status_code == 403
    assert (await client.put(f"{url}/settings", headers=headers, json={
        "currency": "INR", "target_amount": "1000",
    })).status_code == 200
    added = await client.post(f"{url}/items", headers=headers, json={
        "label": "Airport transfer", "category": "travel", "place_name": "Osaka",
        "quantity": "2", "unit_amount": "125.50", "is_included": True,
    })
    assert added.status_code == 201
    assert added.json()["priced_total"] == "251.00"
    assert (await client.post(f"{url}/lock", headers=headers,
                              json={"accept_suggestions": False})).status_code == 409  # no finalized budget before a plan
    item_id = added.json()["items"][0]["id"]
    assert (await client.put(f"{url}/settings", headers=headers, json={
        "currency": "USD", "target_amount": "1000",
    })).status_code == 409  # No silent relabeling of priced INR amounts.
    assert (await client.post(f"{url}/items", headers=headers, json={
        "label": "Bad", "category": "food", "quantity": "1", "unit_amount": "-1",
    })).status_code == 422
    excluded = await client.put(f"{url}/items/{item_id}", headers=headers, json={
        "label": "Airport transfer", "category": "travel", "place_name": "Osaka",
        "quantity": "2", "unit_amount": "125.50", "is_included": False,
    })
    assert excluded.json()["priced_total"] == "0.00"
    assert (await client.delete(f"{url}/items/{item_id}", headers=headers)).status_code == 200
    assert (await client.get(url, headers=headers)).json()["items"] == []


def test_stay_and_food_rows_count_nights_and_days():
    graph = itinerary(["Tokyo", "Kyoto"])
    graph["nodes"][1].update(day_start=1, day_end=4)
    graph["nodes"][2].update(day_start=5, day_end=7)
    rows = {row["label"]: row["quantity"] for row in _desired_rows(graph).values()}
    assert (rows["Stay in Tokyo"], rows["Food in Tokyo"]) == (4, 4)
    assert (rows["Stay in Kyoto"], rows["Food in Kyoto"], rows["Activities in Kyoto"]) == (2, 3, 1)


@pytest.mark.asyncio
async def test_estimates_are_suggested_projected_then_accepted(client: AsyncClient):
    from unittest.mock import AsyncMock, patch
    import json

    headers = {"X-Guest-ID": str(uuid.uuid4())}
    created = (await client.post("/api/trips", headers=headers)).json()
    trip_id = created["trip_id"]
    async with TestingSessionLocal() as db:
        db.add(ConversationMessage(
            session_id=uuid.UUID(created["session_id"]), role=MessageRole.ASSISTANT,
            message_type=MessageType.TEXT, content="Trip draft",
            payload={"kind": "ITINERARY_GRAPH", "graph": itinerary(["Kyoto"])}))
        await db.commit()
    rows = (await client.get(f"/api/trips/{trip_id}/budget", headers=headers)).json()["items"]
    stay = next(row for row in rows if row["category"] == "stay")
    await client.put(f"/api/trips/{trip_id}/budget/items/{stay['id']}", headers=headers, json={
        "label": stay["label"], "category": "stay", "quantity": "1", "unit_amount": "5000"})

    async def fake(prompt, **_):
        return json.dumps({"estimates": [{"id": row["id"], "amount": 1000, "note": "Reddit: cheap"}
                                         for row in json.loads(prompt)["ROWS"]] + [{"id": "nope", "amount": 1}]})

    with patch("app.agents.trip_planner.copilot.research.community_search", new=AsyncMock(return_value=[])), \
            patch("app.services.llm.service.llm_service.generate", side_effect=fake):
        estimated = (await client.post(f"/api/trips/{trip_id}/budget/estimates", headers=headers)).json()
    assert estimated["priced_total"] == "5000.00"  # suggestions are not counted as entered money
    assert estimated["projected_total"] == "9000.00"  # + food, local travel, activities, one leg
    assert estimated["unestimated_count"] == 0
    accepted = (await client.post(f"/api/trips/{trip_id}/budget/estimates/accept", headers=headers)).json()
    assert accepted["priced_total"] == accepted["projected_total"] == "9000.00"
    assert next(r for r in accepted["items"] if r["id"] == stay["id"])["unit_amount"] == "5000.00"


def test_single_stop_without_day_ranges_covers_the_whole_trip():
    rows = {row["label"]: row["quantity"] for row in _desired_rows(itinerary(["Kyoto"]), trip_days=3).values()}
    assert (rows["Stay in Kyoto"], rows["Food in Kyoto"], rows["Local travel in Kyoto"]) == (2, 3, 3)


@pytest.mark.asyncio
async def test_final_budget_is_persistent_read_only_and_unlockable_in_chat(client: AsyncClient):
    headers = {"X-Guest-ID": str(uuid.uuid4())}
    created = (await client.post("/api/trips", headers=headers)).json()
    trip_id = created["trip_id"]
    async with TestingSessionLocal() as db:
        trip = await db.get(Trip, uuid.UUID(trip_id))
        trip.status = TripStatus.PLANNING
        db.add(ConversationMessage(session_id=uuid.UUID(created["session_id"]), role=MessageRole.ASSISTANT,
                                   message_type=MessageType.TEXT, content="Trip draft",
                                   payload={"kind": "ITINERARY_GRAPH", "graph": itinerary(["Kyoto"])}))
        await db.commit()
    url = f"/api/trips/{trip_id}/budget"
    initial = (await client.get(url, headers=headers)).json()
    assert (await client.post(f"{url}/lock", headers=headers, json={"accept_suggestions": False})).status_code == 409
    for item in initial["items"]:
        saved = await client.put(f"{url}/items/{item['id']}", headers=headers, json={
            "label": item["label"], "category": item["category"], "place_name": item["place_name"],
            "quantity": item["quantity"], "unit_amount": "100", "is_included": True})
        assert saved.status_code == 200
    locked = (await client.post(f"{url}/lock", headers=headers, json={"accept_suggestions": False})).json()
    assert locked["is_locked"] is True and locked["locked_total"] == locked["priced_total"]
    assert (await client.get(url, headers=headers)).json()["locked_total"] == locked["locked_total"]
    assert (await client.put(f"{url}/settings", headers=headers,
                             json={"currency": "INR", "target_amount": "999"})).status_code == 409
    assert (await client.post(f"{url}/items", headers=headers,
                              json={"label": "Taxi", "category": "travel", "unit_amount": "50"})).status_code == 409
    assert (await client.put(f"{url}/items/{initial['items'][0]['id']}", headers=headers,
                             json={"label": "Changed", "category": "travel", "unit_amount": "1"})).status_code == 409
    assert (await client.post(f"{url}/estimates", headers=headers)).status_code == 409
    doc = (await client.get(f"/api/trips/{trip_id}/document", headers=headers)).json()
    assert doc["budget"]["is_locked"] is True and doc["budget"]["locked_total"] == float(locked["locked_total"])
    refused = (await client.post(f"/api/trips/{trip_id}/messages", headers=headers,
                                 json={"message_type": "TEXT", "content": "Move Kyoto to day 2"})).json()
    assert "Unlock it" in refused["assistant_message"]["content"]
    unlocked = (await client.post(f"/api/trips/{trip_id}/messages", headers=headers,
                                  json={"message_type": "TEXT", "content": "Unlock the budget"})).json()
    assert "unlocked" in unlocked["assistant_message"]["content"].lower()
    assert (await client.get(url, headers=headers)).json()["is_locked"] is False
    async with TestingSessionLocal() as db:
        relocked = [event async for event in conversation_service.process_message_stream(
            db, uuid.UUID(trip_id), SendMessageRequest(message_type=MessageType.TEXT, content="Lock this budget"),
            RequestIdentity(guest_id=headers["X-Guest-ID"]))]
    assert any("Budget locked" in event for event in relocked)
    assert (await client.get(url, headers=headers)).json()["is_locked"] is True
