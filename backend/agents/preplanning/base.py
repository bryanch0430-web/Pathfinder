"""Base class for the four pre-planning agents.

Each agent owns one TripPlan section and only the tools on its allowlist. The model proposes the
agent's tool calls (that is where tool use and repair happen); if the model goes off task (for
example an injected instruction makes it propose only a disallowed tool), the agent still issues
its own canonical request so it "still returns its own task output" (proposal, injection
containment criterion).
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from backend.agents.prompts import AGENT_TOOLS_INSTRUCTION, Purpose, data_message, system
from backend.agents.runtime.caller import ModelCallError, OutputRejected
from backend.agents.runtime.deps import RuntimeDeps
from backend.agents.runtime.structured import StructuredOutputError, complete_structured
from backend.agents.runtime.tool_use import ToolCallPlan, call_tool_with_repair, operation_of
from backend.schemas.agents import AgentResult, AgentTask
from backend.schemas.common import AgentName, PathName, SectionStatus
from backend.schemas.observability import TraceContext
from backend.schemas.tools import ToolOperation, ToolOutcome, ToolOutcomeStatus, ToolRequest
from backend.schemas.trip_plan import SourceRef
from backend.tools.gateway import ScopedToolGateway


@dataclass
class AgentRun:
    """Per-run state: the scoped gateway, the deadline and every tool call id made."""

    gateway: ScopedToolGateway
    trace: TraceContext
    path: PathName
    deadline: float
    tool_call_ids: list[str] = field(default_factory=list)

    def remaining(self) -> float:
        return max(0.0, self.deadline - asyncio.get_running_loop().time())

    def track(self, outcome: ToolOutcome) -> ToolOutcome:
        self.tool_call_ids.append(outcome.call_id)
        return outcome


def source_of(outcome: ToolOutcome) -> SourceRef:
    if outcome.tool is None or outcome.fetched_at is None or outcome.provider is None:
        raise ValueError("source_of() needs a successful outcome")
    return SourceRef(
        tool=outcome.tool,
        call_id=outcome.call_id,
        provider=outcome.provider,
        fetched_at=outcome.fetched_at,
    )


def oldest_fetch(outcomes: Sequence[ToolOutcome]) -> datetime | None:
    stamps = [o.fetched_at for o in outcomes if o.ok and o.fetched_at is not None]
    return min(stamps) if stamps else None


def failure_reason(outcome: ToolOutcome) -> str:
    kind = outcome.failure.value if outcome.failure else outcome.status.value
    op = outcome.operation.value if outcome.operation else "tool call"
    return f"{op} {kind}; data unavailable"


class PreplanningAgent(ABC):
    name: ClassVar[AgentName]
    tools_purpose: ClassVar[Purpose]
    tools_system: ClassVar[str]
    expected_operations: ClassVar[frozenset[ToolOperation]]

    def __init__(self, deps: RuntimeDeps) -> None:
        self.deps = deps

    async def run(
        self, task: AgentTask, *, trace: TraceContext, path: PathName, deadline: float
    ) -> AgentResult:
        run = AgentRun(
            gateway=self.deps.tools.scoped(path=path, agent=self.name, trace=trace),
            trace=trace,
            path=path,
            deadline=deadline,
        )
        try:
            result = await self._run(task, run)
        except (ModelCallError, StructuredOutputError, OutputRejected) as exc:
            result = AgentResult(
                agent=self.name,
                status=SectionStatus.UNAVAILABLE,
                reason=f"model step failed ({type(exc).__name__}); data unavailable",
            )
        result.tool_call_ids = list(dict.fromkeys([*run.tool_call_ids, *result.tool_call_ids]))
        return result

    @abstractmethod
    async def _run(self, task: AgentTask, run: AgentRun) -> AgentResult: ...

    @abstractmethod
    def canonical_requests(self, task: AgentTask) -> list[ToolRequest]:
        """The agent's own task expressed as code-built requests (off-task fallback)."""

    def normalise_raw(self, raw: str, task: AgentTask) -> str:
        """Hook to fill in task facts the model left out of a proposed call (default: none).
        Only adds missing fields; it never changes what the model did send."""
        return raw

    def unavailable(self, reason: str) -> AgentResult:
        return AgentResult(agent=self.name, status=SectionStatus.UNAVAILABLE, reason=reason)

    async def propose_and_call(self, task: AgentTask, run: AgentRun) -> list[ToolOutcome]:
        """Model proposes tool calls -> each executed with bounded repair. Proposals for other
        agents' tools are still sent to the gateway, where the allowlist blocks and logs them."""
        settings = self.deps.settings
        messages = [
            system(self.tools_system, self.deps.canary),
            data_message(AGENT_TOOLS_INSTRUCTION, [("agent_task", task.model_dump_json())]),
        ]
        raws: list[str] = []
        try:
            proposal = await complete_structured(
                self.deps.agent_caller,
                purpose=self.tools_purpose,
                messages=messages,
                schema=ToolCallPlan,
                trace=run.trace,
                path=run.path,
                agent=self.name,
                max_repairs=settings.max_model_repairs,
            )
            raws = [self.normalise_raw(json.dumps(call), task) for call in proposal.tool_calls]
        except (OutputRejected, StructuredOutputError):
            raws = []

        outcomes: list[ToolOutcome] = []
        for raw in raws:
            outcome = await call_tool_with_repair(
                caller=self.deps.agent_caller,
                gateway=run.gateway,
                raw_request=raw,
                purpose=self.tools_purpose,
                base_messages=messages,
                trace=run.trace,
                path=run.path,
                agent=self.name,
                max_repairs=settings.max_model_repairs,
                budget_s=run.remaining(),
            )
            outcomes.append(run.track(outcome))

        on_task = any(
            operation_of(raw) in {op.value for op in self.expected_operations} for raw in raws
        )
        if not on_task:
            self.deps.observability.record_event(
                run.trace, "agent_off_task_fallback", {"agent": self.name.value}
            )
            for request in self.canonical_requests(task):
                outcomes.append(run.track(await run.gateway.call(request, budget_s=run.remaining())))
        return [o for o in outcomes if o.status is not ToolOutcomeStatus.BLOCKED]

    async def call(self, run: AgentRun, request: ToolRequest) -> ToolOutcome:
        """A code-built follow-up call (e.g. geocoding each place)."""
        return run.track(await run.gateway.call(request, budget_s=run.remaining()))
