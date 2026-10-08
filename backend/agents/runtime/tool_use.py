"""Model-proposed tool calls with bounded repair.

Proposal §4: "Invalid JSON, a missing field, or a date written as free text is not sent again
unchanged. An adapter returns a structured validation error, and the model may repair it at
most twice." The gateway produces the structured error; this loop shows it to the model and
re-submits only a CHANGED request. An identical resubmission counts as a failed repair and is
not sent.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from pydantic import BaseModel, JsonValue

from backend.agents.runtime.caller import ModelCaller
from backend.agents.runtime.structured import repair_message, strip_json_fence
from backend.schemas.common import AgentName, PathName
from backend.schemas.llm import ChatMessage, Role
from backend.schemas.observability import TraceContext
from backend.schemas.tools import ToolOutcome, ToolOutcomeStatus
from backend.tools.gateway import ScopedToolGateway


class ToolCallPlan(BaseModel):
    """Envelope a model returns when asked which tool calls to make. Each element is validated
    individually by the gateway so one bad call yields its own structured error."""

    tool_calls: list[dict[str, JsonValue]]


def _canonical(raw: str) -> str:
    try:
        return json.dumps(json.loads(strip_json_fence(raw)), sort_keys=True)
    except json.JSONDecodeError:
        return raw.strip()


def tool_repair_purpose(purpose: str) -> str:
    """'weather.tool_call' -> 'weather.tool_repair' (distinct from a structured-output repair of
    the proposal envelope, which is 'weather.tool_call.repair')."""
    if purpose.endswith(".tool_call"):
        return purpose[: -len(".tool_call")] + ".tool_repair"
    return f"{purpose}.tool_repair"


def operation_of(raw: str) -> str | None:
    """Best-effort read of the proposed operation name, for on-task checks."""
    try:
        data = json.loads(strip_json_fence(raw))
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        op = data.get("operation")
        return op if isinstance(op, str) else None
    return None


async def call_tool_with_repair(
    *,
    caller: ModelCaller,
    gateway: ScopedToolGateway,
    raw_request: str,
    purpose: str,
    base_messages: Sequence[ChatMessage],
    trace: TraceContext,
    path: PathName,
    agent: AgentName | None,
    max_repairs: int,
    budget_s: float | None,
) -> ToolOutcome:
    raw = strip_json_fence(raw_request)
    outcome = await gateway.call(raw, budget_s=budget_s)
    repairs = 0
    while outcome.status is ToolOutcomeStatus.VALIDATION_ERROR and repairs < max_repairs:
        repairs += 1
        messages = [
            *base_messages,
            ChatMessage(role=Role.ASSISTANT, content=raw),
            repair_message(outcome.issues, what="tool call"),
        ]
        response = await caller.call(
            purpose=tool_repair_purpose(purpose), messages=messages, trace=trace, path=path, agent=agent
        )
        repaired = strip_json_fence(response.content)
        if _canonical(repaired) == _canonical(raw):
            # Never resend an unchanged invalid request.
            continue
        raw = repaired
        outcome = await gateway.call(raw, budget_s=budget_s)
    return outcome
