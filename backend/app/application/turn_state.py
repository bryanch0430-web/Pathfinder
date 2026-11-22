"""The live state of one turn, and the checkpoint it leaves behind.

A turn used to be assembled from pieces across the orchestrator: the context here, the plan there,
the route decision passed as an argument, the gaps read back off the plan. That works until something
goes wrong halfway through, and then nothing on the way out says which part had finished. This module
makes the turn one typed object (``TurnState``) with an explicit phase, so the orchestrator updates it
as it goes instead of re-deriving the same facts, and so the store can keep it as a ``TurnCheckpoint``.

The checkpoint is deliberately small in meaning, not in bytes: the route decision, the plan a turn has
built so far, the reply it drafted, and the parts it was changing. It is written at three points
(robustness steps 2 and 3 of the upgrade note):

1. ``routed``   - Jev (or the writer, or the rules) has produced the ``RouteDecision``.
2. ``agents``   - the specialist agents have finished, before the planner merges their evidence.
3. ``ready``    - validation/repair has passed and the turn is about to persist its plan.

A checkpoint older than ``settings.turn_checkpoint_ttl_seconds``, for another message, or made against
another plan version is not a continuation and is ignored: a resumed turn must still describe the same
trip. A completed turn removes its checkpoint, so the row only survives a turn that was cut off.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.models import (
    AgentResult,
    DataGap,
    Message,
    RouteDecision,
    TripContext,
    TripPlan,
    utcnow,
)

#: How far a turn has got. ``routing`` and ``clarifying`` are before any evidence is gathered;
#: ``planning`` and ``modifying`` are the two paths that run agents; ``validating`` is the check
#: and repair step both paths end with; ``done`` and ``error`` close the turn.
TurnPhase = Literal[
    "routing",
    "clarifying",
    "planning",
    "modifying",
    "validating",
    "done",
    "error",
]

#: Phases a checkpoint may be written in, in the order a turn reaches them.
CHECKPOINT_PHASES: tuple[str, ...] = ("routed", "agents", "planned", "ready")

#: A checkpoint in one of these phases holds enough to finish the turn without running the agents again.
RESUMABLE_PHASES: frozenset[str] = frozenset({"agents", "planned", "ready"})


def hash_message(message: str) -> str:
    """What a checkpoint is a checkpoint *of*: the message, normalised so spacing does not matter."""
    text = re.sub(r"\s+", " ", (message or "").strip()).casefold()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class TurnState(BaseModel):
    """One turn, from screening to persist. The fields mirror the steps the orchestrator takes.

    ``phase`` is the literal phase the turn is in; ``checkpoint_phase`` is the last checkpoint written,
    which is what a resume reads. ``agent_results`` is filled by the plan path's ``on_results`` hook and
    ``data_gaps`` is read off the plan once it exists, so a failed turn can still say what was missing.
    """

    session_id: str
    request_id: str
    trip_context: TripContext
    message: str = ""
    message_hash: str = ""
    messages: list[Message] = Field(default_factory=list)
    current_plan: TripPlan | None = None
    #: The version the turn started from (0 when it started a trip). A checkpoint may only be resumed
    #: against this version: if the plan has moved since, its stored plan is stale.
    base_plan_version: int = 0
    #: The version the turn now holds (``current_plan.version`` once there is one).
    plan_version: int = 0
    route_decision: RouteDecision | None = None
    phase: TurnPhase = "routing"
    checkpoint_phase: str = ""
    affected_parts: list[str] = Field(default_factory=list)
    missing_slots: list[str] = Field(default_factory=list)
    agent_results: dict[str, AgentResult] = Field(default_factory=dict)
    data_gaps: list[DataGap] = Field(default_factory=list)
    clarification_question: str | None = None
    errors: list[str] = Field(default_factory=list)
    #: The reply this turn wrote (or drafted). A resume persists it instead of asking the writer again.
    reply: str = ""
    #: Changes waiting for the traveller's tick (a proposal), carried into a resume unchanged.
    pending: list[dict[str, Any]] = Field(default_factory=list)
    checkpointed_at: datetime | None = None

    @classmethod
    def begin(
        cls,
        *,
        session_id: str,
        request_id: str,
        message: str,
        context: TripContext,
        history: list[Message] | None = None,
        plan: TripPlan | None = None,
    ) -> "TurnState":
        return cls(
            session_id=session_id,
            request_id=request_id,
            message=message,
            message_hash=hash_message(message),
            trip_context=context.model_copy(deep=True),
            messages=list(history or []),
            current_plan=plan,
            base_plan_version=plan.version if plan is not None else 0,
            plan_version=plan.version if plan is not None else 0,
        )

    # -- the steps the orchestrator reports as the turn runs -----------------
    def record_route(self, decision: RouteDecision) -> None:
        self.route_decision = decision
        self.affected_parts = list(decision.affected_parts)
        self.missing_slots = list(decision.missing_slots)

    def record_agents(self, results: dict[str, AgentResult]) -> None:
        self.agent_results = dict(results or {})

    def record_plan(self, plan: TripPlan) -> None:
        self.current_plan = plan
        self.plan_version = plan.version
        self.data_gaps = list(plan.data_quality.gaps)

    def record_clarification(self, question: str, slots: list[str]) -> None:
        self.phase = "clarifying"
        self.clarification_question = question
        self.missing_slots = list(slots)

    def record_error(self, message: str) -> None:
        """Keep the failure, without repeats: one turn may report the same timeout twice."""
        text = (message or "").strip()
        if text and text not in self.errors:
            self.errors.append(text)
        self.phase = "error"

    def start(self, phase: TurnPhase) -> "TurnState":
        self.phase = phase
        return self

    def finish(self) -> "TurnState":
        if self.phase != "error":
            self.phase = "done"
        return self


class TurnCheckpoint(BaseModel):
    """A turn as the store keeps it. Enough to finish the turn, not enough to be a second source of truth."""

    request_id: str
    session_id: str
    phase: str
    message: str = ""
    message_hash: str = ""
    base_plan_version: int = 0
    decision: RouteDecision | None = None
    plan: TripPlan | None = None
    reply: str = ""
    pending: list[dict[str, Any]] = Field(default_factory=list)
    affected_parts: list[str] = Field(default_factory=list)
    missing_slots: list[str] = Field(default_factory=list)
    #: The evidence the specialist agents had already gathered when the ``agents`` checkpoint was written.
    #: A resume re-composes the plan from it, so a turn cut off in the planner does not repeat a tool call.
    agent_results: dict[str, AgentResult] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime = Field(default_factory=utcnow)

    @classmethod
    def from_state(cls, state: TurnState, *, phase: str, ttl_seconds: int) -> "TurnCheckpoint":
        now = utcnow()
        return cls(
            request_id=state.request_id,
            session_id=state.session_id,
            phase=phase,
            message=state.message,
            message_hash=state.message_hash or hash_message(state.message),
            base_plan_version=state.base_plan_version,
            decision=state.route_decision,
            plan=state.current_plan,
            reply=state.reply,
            pending=list(state.pending),
            affected_parts=list(state.affected_parts),
            missing_slots=list(state.missing_slots),
            agent_results=dict(state.agent_results),
            created_at=now,
            expires_at=now + timedelta(seconds=max(0, ttl_seconds)),
        )

    def is_expired(self, now: datetime | None = None) -> bool:
        return _aware(self.expires_at) <= (now or utcnow())

    @property
    def can_finish(self) -> bool:
        """True when the checkpoint holds what it takes to write the turn's result itself."""
        if self.phase in {"planned", "ready"}:
            return self.plan is not None
        if self.phase == "agents":
            return bool(self.agent_results)
        return False

    def is_continuation(self, message: str, plan_version: int, *, now: datetime | None = None) -> bool:
        """True when this checkpoint finishes *this* message against *this* plan, and is still fresh.

        A different message is a new request, not a retry. A different plan version means the trip has
        moved since the checkpoint was made, so its plan is stale and finishing from it would drop a
        version. An old checkpoint is a different conversation.
        """
        return (
            self.phase in RESUMABLE_PHASES
            and self.message_hash == hash_message(message)
            and self.base_plan_version == plan_version
            and not self.is_expired(now)
        )

    def finishes(self, message: str, stored_version: int, *, now: datetime | None = None) -> bool:
        """True when this checkpoint finishes *this* message, whether or not its plan is already stored.

        A stored version matching the checkpoint's own plan means the turn was cut off after it saved:
        the reply is missing, not the work. A ``planned``/``ready`` checkpoint then replays instead of
        the turn being run again. A different message, phase, or an old checkpoint is nothing.
        """
        if self.phase not in RESUMABLE_PHASES or self.is_expired(now):
            return False
        if self.message_hash != hash_message(message):
            return False
        if self.base_plan_version == stored_version:
            return True
        if self.phase in {"planned", "ready"} and self.plan is not None:
            return self.plan.version == stored_version
        return False

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, raw: str) -> "TurnCheckpoint":
        return cls.model_validate_json(raw)


def checkpoint_for(state: TurnState, *, phase: str, ttl_seconds: int) -> TurnCheckpoint:
    """The checkpoint to write for ``state`` now. Kept as a function so callers cannot forget the TTL."""
    return TurnCheckpoint.from_state(state, phase=phase, ttl_seconds=ttl_seconds)


def resumed_state(checkpoint: TurnCheckpoint, *, request_id: str, context: TripContext) -> TurnState:
    """The state a resumed turn continues from: the checkpoint's plan, decision and request for this turn."""
    state = TurnState(
        session_id=checkpoint.session_id,
        request_id=request_id,
        trip_context=context.model_copy(deep=True),
        message=checkpoint.message,
        message_hash=checkpoint.message_hash,
        current_plan=checkpoint.plan,
        base_plan_version=checkpoint.base_plan_version,
        plan_version=checkpoint.base_plan_version,
        route_decision=checkpoint.decision,
        affected_parts=list(checkpoint.affected_parts),
        missing_slots=list(checkpoint.missing_slots),
        reply=checkpoint.reply,
        pending=list(checkpoint.pending),
        agent_results=dict(checkpoint.agent_results),
        checkpoint_phase=checkpoint.phase,
    )
    return state
