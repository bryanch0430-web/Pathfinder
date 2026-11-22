"""The live turn state and the checkpoint it leaves (robustness steps 1 and 3).

Unit tests for the typed turn object and its checkpoint, plus a store round trip. The end-to-end
resume from a checkpoint is tested in ``test_turn_checkpoints.py``.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

from app.application.turn_state import (
    RESUMABLE_PHASES,
    TurnCheckpoint,
    TurnState,
    checkpoint_for,
    hash_message,
    resumed_state,
)
from app.domain.models import (
    AgentName,
    AgentResult,
    AgentStatus,
    DataGap,
    FailureKind,
    Intent,
    Message,
    RouteDecision,
    TripContext,
    TripPlan,
    utcnow,
)
from app.infrastructure.store import SQLiteStore
from scripts.seed_platform_demo import DESTINATIONS, _plan

SESSION = "sess_state"


def _context() -> TripContext:
    return TripContext(
        session_id=SESSION,
        destination="Kyoto",
        origin="Hong Kong",
        start_date=date(2027, 3, 1),
        end_date=date(2027, 3, 4),
        days=4,
        party_size=2,
        budget=9000,
        currency="HKD",
    )


def _plan_fixture(version: int = 1) -> TripPlan:
    place = next(item for item in DESTINATIONS if item["name"] == "Kyoto")
    plan = _plan(random.Random(1), place, 1)
    return plan.model_copy(update={"session_id": SESSION, "version": version})


def _decision(intent: Intent = Intent.PLAN) -> RouteDecision:
    return RouteDecision(
        intent=intent,
        confidence=0.95,
        reason_code="test",
        affected_parts=["dates"] if intent is Intent.MODIFY else [],
        missing_slots=[],
    )


def test_the_message_hash_ignores_spacing_and_case() -> None:
    assert hash_message("  Plan a  trip to Kyoto ") == hash_message("plan a trip to kyoto")


def test_a_turn_records_the_route_the_evidence_and_the_plan() -> None:
    plan = _plan_fixture()
    state = TurnState.begin(
        session_id=SESSION,
        request_id="req_1",
        message="Plan a trip to Kyoto",
        context=_context(),
        history=[Message(session_id=SESSION, role="user", content="hi")],
        plan=None,
    )
    state.record_route(_decision())
    state.record_agents({"attraction": _result(AgentName.ATTRACTION)})
    state.record_plan(plan)
    state.start("validating")
    state.finish()

    assert state.phase == "done"
    assert state.route_decision is not None and state.route_decision.intent is Intent.PLAN
    assert set(state.agent_results) == {"attraction"}
    assert state.current_plan is not None and state.plan_version == 1
    assert state.message_hash == hash_message("Plan a trip to Kyoto")


def _result(name: AgentName) -> AgentResult:
    return AgentResult(
        agent=name,
        status=AgentStatus.UNAVAILABLE,
        failures=[DataGap(section="attractions", kind=FailureKind.NO_DATA, message="no data")],
    )


def test_the_data_gaps_of_the_plan_are_the_turn_data_gaps() -> None:
    plan = _plan_fixture()
    plan.data_quality.gaps.append(
        DataGap(section="weather", kind=FailureKind.NO_DATA, message="no forecast")
    )
    state = TurnState.begin(session_id=SESSION, request_id="r", message="m", context=_context())
    state.record_plan(plan)

    assert [gap.section for gap in state.data_gaps] == ["weather"]


def test_an_error_is_kept_once_and_moves_the_turn_to_error() -> None:
    state = TurnState.begin(session_id=SESSION, request_id="r", message="m", context=_context())
    state.record_error("the deadline cut the turn off")
    state.record_error("the deadline cut the turn off")

    assert state.phase == "error"
    assert state.errors == ["the deadline cut the turn off"]
    state.finish()
    assert state.phase == "error", "a failure is not finished as done"


def test_a_clarification_is_held_on_the_turn() -> None:
    state = TurnState.begin(session_id=SESSION, request_id="r", message="m", context=_context())
    state.record_clarification("Where are you flying from?", ["origin"])

    assert state.phase == "clarifying"
    assert state.clarification_question == "Where are you flying from?"
    assert state.missing_slots == ["origin"]


def test_a_checkpoint_continues_only_the_same_message_against_the_same_plan() -> None:
    plan = _plan_fixture()
    state = TurnState.begin(
        session_id=SESSION, request_id="req_1", message="Plan a trip", context=_context(), plan=plan
    )
    state.record_route(_decision())
    state.record_plan(plan)
    checkpoint = checkpoint_for(state, phase="planned", ttl_seconds=900)

    assert checkpoint.phase in RESUMABLE_PHASES
    assert checkpoint.is_continuation("Plan a trip", 1)
    assert not checkpoint.is_continuation("Plan a trip to Osaka", 1)
    assert not checkpoint.is_continuation("Plan a trip", 2)


def test_an_expired_checkpoint_is_not_a_continuation() -> None:
    state = TurnState.begin(
        session_id=SESSION, request_id="req_1", message="Plan a trip", context=_context()
    )
    checkpoint = checkpoint_for(state, phase="planned", ttl_seconds=0)

    assert checkpoint.is_expired()
    assert not checkpoint.is_continuation("Plan a trip", 0)


def test_a_checkpoint_whose_plan_is_already_stored_still_finishes_the_turn() -> None:
    plan = _plan_fixture(version=1)
    state = TurnState.begin(session_id=SESSION, request_id="req_1", message="Plan a trip", context=_context())
    state.record_route(_decision())
    state.record_plan(plan)
    checkpoint = checkpoint_for(state, phase="planned", ttl_seconds=60)

    # The turn was cut off after it saved version 1: its base is 0 but its plan is the stored version.
    assert checkpoint.base_plan_version == 0
    assert checkpoint.finishes("Plan a trip", 1)
    assert not checkpoint.finishes("Plan a trip", 2)
    # An agents checkpoint is not replayed: it re-composes from its evidence, not from a stored plan.
    assert not checkpoint_for(state, phase="agents", ttl_seconds=60).finishes("Plan a trip", 1)


def test_a_checkpoint_can_finish_only_what_it_holds() -> None:
    plan = _plan_fixture()
    state = TurnState.begin(session_id=SESSION, request_id="r", message="m", context=_context(), plan=plan)
    planned = checkpoint_for(state, phase="planned", ttl_seconds=60)
    agents = checkpoint_for(state, phase="agents", ttl_seconds=60)
    routed = checkpoint_for(state, phase="routed", ttl_seconds=60)

    assert planned.can_finish
    assert not agents.can_finish, "no evidence was recorded"
    state.record_agents({"attraction": _result(AgentName.ATTRACTION)})
    assert checkpoint_for(state, phase="agents", ttl_seconds=60).can_finish
    assert not routed.can_finish


def test_a_resumed_state_carries_the_decision_and_the_plan() -> None:
    plan = _plan_fixture()
    state = TurnState.begin(session_id=SESSION, request_id="r", message="m", context=_context(), plan=plan)
    state.record_route(_decision(Intent.MODIFY))
    state.reply = "Updated to version 1."
    checkpoint = checkpoint_for(state, phase="ready", ttl_seconds=60)

    resumed = resumed_state(checkpoint, request_id="req_2", context=_context())

    assert resumed.request_id == "req_2"
    assert resumed.route_decision is not None and resumed.route_decision.intent is Intent.MODIFY
    assert resumed.current_plan is not None and resumed.current_plan.version == 1
    assert resumed.reply == "Updated to version 1."
    assert resumed.checkpoint_phase == "ready"


def test_the_store_round_trips_a_checkpoint_and_drops_it(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "checkpoints.db")
    plan = _plan_fixture()
    state = TurnState.begin(session_id=SESSION, request_id="req_1", message="Go", context=_context(), plan=plan)
    state.record_route(_decision())
    state.record_plan(plan)
    checkpoint = checkpoint_for(state, phase="planned", ttl_seconds=900)

    store.save_turn_checkpoint(
        request_id=checkpoint.request_id,
        session_id=checkpoint.session_id,
        phase=checkpoint.phase,
        message_hash=checkpoint.message_hash,
        base_plan_version=checkpoint.base_plan_version,
        data=checkpoint.to_json(),
        expires_at=checkpoint.expires_at,
    )

    row = store.latest_turn_checkpoint(SESSION)
    assert row is not None
    restored = TurnCheckpoint.from_json(row["data"])
    assert restored.phase == "planned"
    assert restored.plan is not None and restored.plan.version == 1
    assert restored.decision is not None

    store.drop_turn_checkpoints(SESSION)
    assert store.latest_turn_checkpoint(SESSION) is None


def test_an_expired_checkpoint_is_not_returned_by_the_store(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "checkpoints.db")
    expired = TurnCheckpoint(
        request_id="req_old",
        session_id=SESSION,
        phase="routed",
        expires_at=utcnow() - timedelta(minutes=1),
    )
    store.save_turn_checkpoint(
        request_id=expired.request_id,
        session_id=expired.session_id,
        phase=expired.phase,
        message_hash="",
        base_plan_version=0,
        data=expired.to_json(),
        expires_at=expired.expires_at,
    )

    assert store.latest_turn_checkpoint(SESSION) is None
