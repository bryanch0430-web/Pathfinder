"""Call records. Proposal Appendix B: "The system must log every web search and API call to
guarantee that its recommendations are based on real data". These records are the tool log
the grounding metric joins against, and what is mirrored to Langfuse."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from backend.schemas.common import AgentName, PathName, StrictModel, ToolName, utcnow
from backend.schemas.llm import LLMRequest, TokenUsage
from backend.schemas.tools import (
    FailureKind,
    FieldIssue,
    ToolOperation,
    ToolOutcomeStatus,
    ToolPayload,
)

AttributeValue = str | int | float | bool


class TraceContext(StrictModel):
    trace_id: str
    session_id: str
    name: str
    started_at: datetime = Field(default_factory=utcnow)


class LLMCallRecord(StrictModel):
    record_id: str
    trace_id: str
    purpose: str
    path: PathName | None = None
    agent: AgentName | None = None
    model: str
    request: LLMRequest = Field(description="Exactly what was sent")
    response_content: str | None = None
    usage: TokenUsage | None = None
    error: str | None = None
    started_at: datetime
    ended_at: datetime
    latency_ms: float


class ToolCallRecord(StrictModel):
    record_id: str
    trace_id: str
    call_id: str
    path: PathName
    agent: AgentName | None = None
    operation: ToolOperation | None = None
    tool: ToolName | None = None
    raw_request: str = Field(description="The request exactly as proposed (JSON text)")
    status: ToolOutcomeStatus
    failure: FailureKind | None = None
    issues: list[FieldIssue] = Field(default_factory=list)
    attempts: int = 0
    provider: str | None = None
    payload: ToolPayload | None = None
    fetched_at: datetime | None = None
    started_at: datetime
    ended_at: datetime
    latency_ms: float


class TraceEvent(StrictModel):
    trace_id: str
    name: str
    at: datetime = Field(default_factory=utcnow)
    attributes: dict[str, AttributeValue] = Field(default_factory=dict)


class TurnMetrics(StrictModel):
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    agents_run: list[AgentName] = Field(default_factory=list)
