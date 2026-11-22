"""The clarification gate (robustness step 5): a still-ambiguous answer is asked about again.

The router keeps deciding the path. The gate tightens one escape: after a clarify that asked *what
to do*, a bare "yes"/"maybe" must not be planned from just because the trip form happens to be full.
"""

from __future__ import annotations

import asyncio

from app.application.turn_gate import (
    is_bare_answer,
    last_asked_for_intent,
    tighten_after_clarification,
)
from app.config import Settings
from app.domain.models import EvalCase, Intent, Message, RouteDecision
from app.eval_efficiency import build_rig, case_context
from app.eval_metering import RunLedger

SESSION = "sess_gate"


def _assistant(meta: dict | None = None, content: str = "What would you like to do?") -> Message:
    return Message(session_id=SESSION, role="assistant", content=content, intent=Intent.UNCLEAR, meta=meta or {})


def _user(text: str) -> Message:
    return Message(session_id=SESSION, role="user", content=text)


def _stored(session_id: str, message: Message) -> Message:
    """The same row, on the real session the turn runs on."""
    return message.model_copy(update={"session_id": session_id})


def _context_complete_plan_decision() -> RouteDecision:
    return RouteDecision(
        intent=Intent.PLAN,
        confidence=0.75,
        need_clarification=False,
        reason_code="context_complete",
    )


def test_an_intent_question_is_a_clarify_with_no_slots() -> None:
    assert last_asked_for_intent([_user("hi"), _assistant({"clarify": True, "slots": []})])
    assert not last_asked_for_intent([_user("hi"), _assistant({"clarify": True, "slots": ["origin"]})])
    # The last row is the message being answered now, so the question before it still counts.
    assert last_asked_for_intent([_user("hi"), _assistant({"clarify": True, "slots": []}), _user("ok")])
    assert not last_asked_for_intent([])


def test_a_bare_answer_says_nothing_about_the_trip() -> None:
    for text in ("yes", "yes please", "maybe", "not sure", "OK", "no", "I don't know"):
        assert is_bare_answer(text), text
    for text in ("yes, plan a trip to Kyoto", "cheaper", "day 2", "4 nights", "why is that", "plan it"):
        assert not is_bare_answer(text), text


def test_a_bare_answer_after_an_intent_question_is_asked_again() -> None:
    history = [_user("hmm"), _assistant({"clarify": True, "slots": []})]

    tightened = tighten_after_clarification(
        _context_complete_plan_decision(), message="maybe", history=history
    )

    assert tightened.intent is Intent.UNCLEAR
    assert tightened.need_clarification
    assert tightened.reason_code == "still_unclear_after_clarify"


def test_a_real_request_is_never_held_back() -> None:
    history = [_user("hmm"), _assistant({"clarify": True, "slots": []})]
    decision = _context_complete_plan_decision()

    assert tighten_after_clarification(decision, message="plan a trip to Kyoto", history=history) is decision


def test_the_gate_only_touches_its_own_context_complete_escape() -> None:
    history = [_user("hmm"), _assistant({"clarify": True, "slots": []})]
    real = RouteDecision(intent=Intent.PLAN, confidence=0.95, need_clarification=False, reason_code="new_trip_request")

    assert tighten_after_clarification(real, message="maybe", history=history) is real


def test_without_an_intent_question_a_fragment_is_left_to_the_router() -> None:
    decision = _context_complete_plan_decision()

    assert tighten_after_clarification(decision, message="maybe", history=[]) is decision


def test_a_bare_answer_after_an_intent_clarify_is_asked_about_in_a_turn(settings: Settings) -> None:
    """The gate through the real turn service: no plan is made from "maybe"."""
    settings = settings.model_copy(update={"preplan_budget_seconds": 10, "agent_timeout_seconds": 5})
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        session = ledger.store.create_session("guest-gate")
        context = case_context(
            EvalCase(id="gate", dataset="design", input="", destination="Kyoto"),
            session.id,
        )
        ledger.store.save_context(context)
        ledger.store.add_message(_stored(session.id, _assistant({"clarify": True, "slots": []})))

        async def turn() -> list:
            return [
                event
                async for event in rig.turns.handle_turn(session_id=session.id, message="maybe")
            ]

        events = asyncio.run(turn())

        intents = [event.data.get("intent") for event in events if event.event == "route"]
        assert intents == ["unclear"], "a bare answer is not planned from"
        assert any(event.event == "clarify" for event in events)
        assert ledger.store.get_plan(session.id) is None
    finally:
        ledger.close()
