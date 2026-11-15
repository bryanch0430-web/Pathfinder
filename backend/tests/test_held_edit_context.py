"""A route edit that waits to be accepted leaves the trip form alone; accepting writes it, rejecting never does."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from plan_samples import make_route_plan
from test_route_edit_turns import Trip, _run

from app.application.proposals import proposal_changes
from app.config import Settings
from app.domain.models import Intent, Message, TripContext, TripStop
from app.interface.api import create_app

THREE = [("Kyoto", 2), ("Osaka", 2), ("Tokyo", 2)]
GUEST = {"X-Client-Token": "test"}


def _held_turn(trip: Trip, message: str) -> list:
    async def go():
        trip.rig.llm.route = {
            "intent": "modify",
            "confidence": 0.95,
            "parts": ["attraction"],
        }
        return [
            e
            async for e in trip.rig.turns.handle_turn(
                session_id=trip.session.id, message=message, review=True
            )
        ]

    return go()


def test_a_held_route_change_leaves_the_trip_form_as_it_was_and_keeps_the_new_one_in_the_proposal(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        before = trip.context.model_copy(deep=True)
        events = await _held_turn(trip, "remove Osaka")
        return before, trip.context, trip.plan, events

    before, after, plan, events = _run(settings, THREE, body)

    held = [e for e in events if e.event == "proposal"]
    assert [c["id"] for c in held[-1].data["proposal"]["changes"]] == ["whole-edit"]
    assert [(s.city, s.nights) for s in after.stops] == [
        (s.city, s.nights) for s in before.stops
    ]
    assert after.destination == before.destination
    assert [s.destination.name for s in plan.segments] == ["Kyoto", "Osaka", "Tokyo"]
    stored = held[-1].data["proposal"]["context"]
    assert [(s["city"], s["nights"]) for s in stored["stops"]] == [
        ("Kyoto", 4),
        ("Tokyo", 2),
    ]


def test_a_route_change_that_is_not_held_still_writes_the_trip_form(
    settings: Settings,
) -> None:
    async def body(trip: Trip):
        await trip.turn("remove Osaka")
        return trip.context

    context = _run(settings, THREE, body)

    assert [(s.city, s.nights) for s in context.stops] == [("Kyoto", 4), ("Tokyo", 2)]


# -- accepting and rejecting, through the route -----------------------------------------------------------------------


@pytest.fixture()
def held(settings: Settings):
    """A client, a trip of three cities, and a held message whose one change drops the middle city."""
    with TestClient(create_app(settings)) as client:
        session_id = client.post("/api/journeys", json={"client_token": "test"}).json()[
            "session"
        ]["id"]
        store = client.app.state.container.store
        old = make_route_plan(["Kyoto", "Osaka", "Tokyo"]).model_copy(
            update={"session_id": session_id}
        )
        new = make_route_plan(["Kyoto", "Tokyo"], nights=3).model_copy(
            update={"session_id": session_id}
        )
        store.save_plan(old, "First plan")
        context = TripContext(
            session_id=session_id,
            destination="Kyoto",
            stops=[
                TripStop(city="Kyoto", nights=2),
                TripStop(city="Osaka", nights=2),
                TripStop(city="Tokyo", nights=2),
            ],
        )
        store.save_context(context)
        edited = context.model_copy(deep=True)
        edited.stops = [
            TripStop(city="Kyoto", nights=3),
            TripStop(city="Tokyo", nights=3),
        ]
        changes = proposal_changes(old, new, summary="Dropped Osaka.")
        message = Message(
            session_id=session_id,
            role="assistant",
            content="This edit is ready and is not written yet.",
            intent=Intent.MODIFY,
            meta={
                "proposal": {
                    "changes": changes,
                    "context": edited.model_dump(mode="json"),
                }
            },
        )
        store.add_message(message)
        yield client, store, session_id, message.id, changes[0]["id"]


def _decide(
    client: TestClient, session_id: str, message_id: str, change_id: str, accept: bool
):
    return client.post(
        f"/api/journeys/{session_id}/proposals/{message_id}",
        json={"change_id": change_id, "accept": accept},
        headers=GUEST,
    )


def test_accepting_the_held_change_writes_the_trip_form_it_carried(held) -> None:
    client, store, session_id, message_id, change_id = held

    response = _decide(client, session_id, message_id, change_id, True)

    assert response.status_code == 200, response.text
    assert [(s.city, s.nights) for s in store.get_context(session_id).stops] == [
        ("Kyoto", 3),
        ("Tokyo", 3),
    ]
    assert [s.destination.name for s in store.get_plan(session_id).segments] == [
        "Kyoto",
        "Tokyo",
    ]


def test_rejecting_the_held_change_leaves_the_trip_form_and_the_plan(held) -> None:
    client, store, session_id, message_id, change_id = held
    version = store.get_plan(session_id).version

    response = _decide(client, session_id, message_id, change_id, False)

    assert response.status_code == 200, response.text
    assert [(s.city, s.nights) for s in store.get_context(session_id).stops] == [
        ("Kyoto", 2),
        ("Osaka", 2),
        ("Tokyo", 2),
    ]
    assert store.get_plan(session_id).version == version
