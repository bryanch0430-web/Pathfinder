"""What a trip teaches Pathfinder about its traveller is kept for the trip's owner.

Once sign-in has moved a guest trip to an account, the trip's owner is `user:<id>`. The preferences a
turn learns used to be written under the old guest token, a profile that anyone holding the token (the
browser restores it on sign-out) can read, and the account's own profile stayed empty.
"""

from __future__ import annotations

import asyncio

from app.application.memory import MemoryService
from app.config import Settings
from app.domain.models import EvalCase, TripContext
from app.eval_efficiency import build_rig, case_context
from app.eval_metering import RunLedger

MESSAGE = "We are vegetarian and love museums."


async def _say(rig, session_id: str) -> list:
    events = [event async for event in rig.turns.handle_turn(session_id=session_id, message=MESSAGE)]
    await rig.turns.drain_background()
    return events


def _session(ledger: RunLedger, token: str) -> str:
    session = ledger.store.create_session(token)
    case = EvalCase(id="t", dataset="memory", input="", destination="Kyoto")
    ledger.store.save_context(case_context(case, session.id))
    return session.id


def test_preferences_learned_in_an_adopted_trip_go_to_the_account(settings: Settings) -> None:
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        session_id = _session(ledger, "guest-adopted")
        assert ledger.store.adopt_sessions("guest-adopted", "user_abc") == 1  # what signing in does
        rig.llm.route = {"intent": "unclear", "confidence": 0.95}

        events = asyncio.run(_say(rig, session_id))
        account = ledger.store.get_preferences("user:user_abc")
        old_guest = ledger.store.get_preferences("guest-adopted")
    finally:
        ledger.close()

    assert "error" not in {event.event for event in events}
    assert account is not None and {"vegetarian", "museums"} <= set(account.preferences)
    assert old_guest is None  # the guest token's profile is not written to any more


def test_a_trip_nobody_owns_remembers_nothing(settings: Settings) -> None:
    """No token and no account is no profile, and a `user:` token that no bearer proved owns nothing."""
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        rig.llm.route = {"intent": "unclear", "confidence": 0.95}
        for token in ("", "user:ua"):
            asyncio.run(_say(rig, _session(ledger, token)))
        stored = [ledger.store.get_preferences(key) for key in ("", "user:ua")]
    finally:
        ledger.close()

    assert stored == [None, None]


def test_remembering_for_no_owner_writes_no_profile(settings: Settings, container) -> None:
    memory = MemoryService(container.store, settings)

    assert memory.remember_preferences("", TripContext(session_id="s", preferences=["food"])) is None

    assert container.store.get_preferences("") is None


def test_preferences_a_turn_learns_after_sign_in_moved_its_trip_go_to_the_account(settings: Settings, monkeypatch) -> None:
    """The traveller signs in from another window while a message is being understood: the turn read its
    trip before that, but what it learns belongs to the owner the trip has when it is learned."""
    from app.application import orchestrator

    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        session_id = _session(ledger, "guest-midturn")
        understood = orchestrator.understand

        async def understand_after_sign_in(*args, **kwargs):
            assert ledger.store.adopt_sessions("guest-midturn", "user_mid") == 1  # what signing in does
            return await understood(*args, **kwargs)

        monkeypatch.setattr(orchestrator, "understand", understand_after_sign_in)
        rig.llm.route = {"intent": "unclear", "confidence": 0.95}

        events = asyncio.run(_say(rig, session_id))
        account = ledger.store.get_preferences("user:user_mid")
        old_guest = ledger.store.get_preferences("guest-midturn")
    finally:
        ledger.close()

    assert "error" not in {event.event for event in events}
    assert account is not None and {"vegetarian", "museums"} <= set(account.preferences)
    assert old_guest is None  # nothing is left on the guest token after sign-in
