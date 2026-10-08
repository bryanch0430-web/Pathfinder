"""Session memory (scenario 20): plan, history and preferences survive across turns."""

from __future__ import annotations

import pytest

from backend.memory.session import (
    InMemorySessionStore,
    SessionNotFound,
    append_turn,
    recent_history,
)
from backend.schemas.common import Money, Route
from backend.schemas.memory import ConversationTurn, PreferenceProfile, SessionState
from backend.schemas.trip import TripContext
from backend.tests.memory.helpers import make_plan


def turn(role: str, content: str) -> ConversationTurn:
    return ConversationTurn(role=role, content=content)  # type: ignore[arg-type]


async def test_s20_plan_history_and_preferences_are_retained_across_turns() -> None:
    store = InMemorySessionStore()
    state = await store.create(TripContext(destination="Kyoto", party_size=2))
    assert len(state.session_id) == 32 and int(state.session_id, 16) >= 0  # uuid4 hex

    # Turn 1: the planner produces a plan, the user states a preference.
    plan = make_plan("Kyoto")
    state.plan = plan
    append_turn(state, ConversationTurn(role="user", content="plan Kyoto", route=Route.PLAN))
    append_turn(state, turn("assistant", "here is a 3 day plan"))
    state.preferences = PreferenceProfile(
        hotel_style="ryokan",
        liked_categories=["temple"],
        budget_hint=Money(amount=800, currency="USD"),
    )
    state.turn_count = 1
    await store.save(state)

    # Turn 2 (a "later turn"): everything is still there.
    later = await store.get(state.session_id)
    assert later is not None
    assert later.plan == plan
    assert [t.content for t in later.history] == ["plan Kyoto", "here is a 3 day plan"]
    assert later.history[0].route is Route.PLAN
    assert later.preferences.hotel_style == "ryokan"
    assert later.preferences.liked_categories == ["temple"]
    assert later.context.destination == "Kyoto"
    assert later.turn_count == 1


async def test_s20_unsaved_mutation_of_a_fetched_copy_does_not_change_the_store() -> None:
    store = InMemorySessionStore()
    state = await store.create()
    state.plan = make_plan("Kyoto")
    append_turn(state, turn("user", "hello"))
    await store.save(state)

    fetched = await store.get(state.session_id)
    assert fetched is not None and fetched.plan is not None
    fetched.plan.destination = "Elsewhere"
    fetched.history.append(turn("user", "half-written turn"))
    fetched.preferences.liked_categories.append("casino")

    again = await store.get(state.session_id)
    assert again is not None and again.plan is not None
    assert again.plan.destination == "Kyoto"
    assert [t.content for t in again.history] == ["hello"]
    assert again.preferences.liked_categories == []
    assert again is not fetched


async def test_save_stores_a_copy_and_bumps_updated_at() -> None:
    store = InMemorySessionStore()
    state = await store.create()
    before = state.updated_at
    state.history.append(turn("user", "x"))
    await store.save(state)
    assert state.updated_at > before

    state.history.append(turn("user", "mutated after save"))  # not saved
    stored = await store.get(state.session_id)
    assert stored is not None
    assert [t.content for t in stored.history] == ["x"]
    assert stored.updated_at == state.updated_at


async def test_create_copies_the_given_context_and_ids_are_unique() -> None:
    store = InMemorySessionStore()
    context = TripContext(destination="Paris")
    a = await store.create(context)
    b = await store.create()
    assert a.session_id != b.session_id
    context.destination = "Changed"
    again = await store.get(a.session_id)
    assert again is not None and again.context.destination == "Paris"
    assert b.context == TripContext()


async def test_unknown_session() -> None:
    store = InMemorySessionStore()
    assert await store.get("nope") is None
    with pytest.raises(SessionNotFound):
        await store.require("nope")
    assert not await store.delete("nope")


def test_append_turn_and_recent_history_window() -> None:
    state = SessionState(session_id="s")
    created = state.updated_at
    for i in range(5):
        append_turn(state, turn("user", f"m{i}"))
    assert len(state.history) == 5  # full history is kept
    assert state.updated_at >= created
    assert [t.content for t in recent_history(state, 3)] == ["m2", "m3", "m4"]
    assert [t.content for t in recent_history(state, 10)] == [f"m{i}" for i in range(5)]
    assert recent_history(state, 0) == []
    window = recent_history(state, 2)
    window.clear()
    assert len(state.history) == 5  # the window is a copy
