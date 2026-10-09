"""System 1 router and clarification gate.

Proposal: "Each incoming message is assembled with that context, the existing TripPlan JSON,
recent conversation history, and the saved preference profile, then passed to a System 1 router
... It returns a typed intent ('plan', 'modify', or 'ask'), a clarity score and a decision on
whether the request needs clarification. If the route probability is at least a set threshold,
say 0.8, the request is sent down that path. Below that threshold, a language-model call drafts
a clarification and the turn returns to the user. An unclear request is not planned."

The router's output channel is typed: anything other than a RouterDecision is rejected
(security.routes), audited, and treated as unclear. Missing key trip variables are decided by
code, not by the model, so they are never guessed.
"""

from __future__ import annotations

import json

from backend.agents.focus import focus_summary
from backend.agents.prompts import (
    ROUTER_FOCUS_INSTRUCTION,
    ROUTER_INSTRUCTION,
    ROUTER_SYSTEM,
    Purpose,
    data_message,
    system,
)
from backend.agents.runtime.caller import ModelCallError, OutputRejected
from backend.agents.runtime.deps import RuntimeDeps
from backend.memory.session import recent_history
from backend.schemas.common import PathName, Route
from backend.schemas.memory import SessionState
from backend.schemas.observability import TraceContext
from backend.schemas.routing import GateReason, GateResult, PlanFocus, RouterDecision
from backend.schemas.security import SecurityEventKind
from backend.security.routes import UntypedRouteError, parse_router_output


def router_blocks(
    state: SessionState, message: str, history_window: int, focus: PlanFocus | None = None
) -> list[tuple[str, str]]:
    history = [
        {"role": t.role, "content": t.content, "route": t.route.value if t.route else None}
        for t in recent_history(state, history_window)
    ]
    blocks = [
        ("trip_context", state.context.model_dump_json()),
        ("current_plan", state.plan.model_dump_json() if state.plan else "null"),
        ("recent_history", json.dumps(history, ensure_ascii=False)),
        ("preference_profile", state.preferences.model_dump_json()),
    ]
    if focus is not None and state.plan is not None:
        blocks.append(("focus", focus_summary(state.plan, focus)))
    blocks.append(("user_message", message))
    return blocks


class System1Router:
    def __init__(self, deps: RuntimeDeps) -> None:
        self.deps = deps

    async def decide(
        self, state: SessionState, message: str, *, trace: TraceContext, focus: PlanFocus | None = None
    ) -> RouterDecision | None:
        """The raw typed decision, or None when the router output was rejected."""
        settings = self.deps.settings
        instruction = ROUTER_FOCUS_INSTRUCTION if focus is not None else ROUTER_INSTRUCTION
        messages = [
            system(ROUTER_SYSTEM, self.deps.canary),
            data_message(instruction, router_blocks(state, message, settings.history_window, focus)),
        ]
        try:
            response = await self.deps.router_caller.call(
                purpose=Purpose.ROUTER, messages=messages, trace=trace, path=PathName.ROUTER
            )
            return parse_router_output(response.content)
        except UntypedRouteError:
            self.deps.audit.record(SecurityEventKind.UNTYPED_ROUTE_REJECTED, path=PathName.ROUTER)
            self.deps.observability.record_event(trace, "router_output_rejected", {"reason": "untyped"})
            return None
        except OutputRejected as exc:
            self.deps.observability.record_event(trace, "router_output_rejected", {"reason": exc.reason})
            return None
        except ModelCallError:
            self.deps.observability.record_event(trace, "router_call_failed", {})
            return None

    def gate(self, state: SessionState, decision: RouterDecision | None) -> GateResult:
        threshold = self.deps.settings.router_confidence_threshold
        if decision is None:
            return GateResult(route=Route.UNCLEAR, reason=GateReason.UNTYPED_OUTPUT)
        if decision.route is Route.UNCLEAR:
            return GateResult(route=Route.UNCLEAR, reason=GateReason.UNCLEAR_ROUTE, decision=decision)
        if decision.confidence < threshold:
            return GateResult(route=Route.UNCLEAR, reason=GateReason.BELOW_THRESHOLD, decision=decision)
        if decision.needs_clarification:
            return GateResult(route=Route.UNCLEAR, reason=GateReason.MODEL_FLAGGED, decision=decision)
        if decision.route is Route.PLAN:
            missing = state.context.missing_key_fields()
            if missing:
                return GateResult(
                    route=Route.UNCLEAR,
                    reason=GateReason.MISSING_FIELDS,
                    decision=decision,
                    missing_fields=missing,
                )
        if decision.route is Route.MODIFY and state.plan is None:
            return GateResult(route=Route.UNCLEAR, reason=GateReason.NO_PLAN_TO_MODIFY, decision=decision)
        return GateResult(route=decision.route, reason=GateReason.ACCEPTED, decision=decision)

    async def route(
        self, state: SessionState, message: str, *, trace: TraceContext, focus: PlanFocus | None = None
    ) -> GateResult:
        decision = await self.decide(state, message, trace=trace, focus=focus)
        result = self.gate(state, decision)
        self.deps.observability.record_event(
            trace,
            "route_decided",
            {
                "route": result.route.value,
                "reason": result.reason.value,
                "confidence": decision.confidence if decision else 0.0,
            },
        )
        return result
