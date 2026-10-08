"""Result of one chat turn, and the events streamed over the WebSocket while it runs."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from backend.schemas.common import AgentName, Route, SectionStatus, StrictModel, utcnow
from backend.schemas.observability import TurnMetrics
from backend.schemas.routing import GateReason, GateResult
from backend.schemas.trip_plan import SourceRef, TripPlan


class TurnErrorCode(StrEnum):
    PLAN_INVALID = "plan_invalid"  # TripPlan failed the schema after bounded repair
    INTERNAL = "internal"


class TurnError(StrictModel):
    code: TurnErrorCode
    message: str


class QuickAnswer(StrictModel):
    text: str
    from_plan: bool = Field(description="True when the current TripPlan already held the fact")
    related_to_plan: bool
    tool_called: bool
    unavailable: bool = Field(default=False, description="The one tool call failed")
    sources: list[SourceRef] = Field(default_factory=list)


class Clarification(StrictModel):
    text: str
    reason: GateReason
    missing_fields: list[str] = Field(default_factory=list)


class TurnResult(StrictModel):
    session_id: str
    trace_id: str
    route: Route
    gate: GateResult
    reply: str
    plan: TripPlan | None = None
    plan_changed: bool = False
    answer: QuickAnswer | None = None
    clarification: Clarification | None = None
    error: TurnError | None = None
    input_flagged: bool = False
    agents_run: list[AgentName] = Field(default_factory=list)
    metrics: TurnMetrics = Field(default_factory=TurnMetrics)


class TurnEventType(StrEnum):
    ROUTE = "route"
    AGENT_STARTED = "agent_started"
    AGENT_FINISHED = "agent_finished"
    PLAN = "plan"
    ANSWER = "answer"
    CLARIFICATION = "clarification"
    ERROR = "error"
    DONE = "done"


class TurnEvent(StrictModel):
    type: TurnEventType
    trace_id: str
    at: datetime = Field(default_factory=utcnow)
    route: Route | None = None
    agent: AgentName | None = None
    status: SectionStatus | None = None
    message: str | None = None
    result: TurnResult | None = Field(default=None, description="Set on the final 'done' event")
