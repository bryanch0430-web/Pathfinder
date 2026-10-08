"""Clarification path: "a language-model call drafts a clarification and the turn returns to the
user. An unclear request is not planned." No tools run here (empty allowlist)."""

from __future__ import annotations

import json

from pydantic import Field

from backend.agents.prompts import CLARIFY_INSTRUCTION, CLARIFY_SYSTEM, Purpose, data_message, system
from backend.agents.runtime.caller import ModelCallError, OutputRejected
from backend.agents.runtime.deps import RuntimeDeps
from backend.agents.runtime.structured import StructuredOutputError, complete_structured
from backend.schemas.common import PathName, StrictModel
from backend.schemas.memory import SessionState
from backend.schemas.observability import TraceContext
from backend.schemas.routing import GateReason, GateResult
from backend.schemas.turn import Clarification

FIELD_QUESTIONS = {
    "destination": "where you would like to go",
    "dates": "your travel dates (start and end)",
    "party_size": "how many people are travelling",
    "budget": "your total budget and currency",
}


class ClarificationDraft(StrictModel):
    question: str = Field(min_length=3, max_length=800)


def fallback_question(gate: GateResult) -> str:
    """Deterministic wording used if the model call fails or its output is rejected."""
    if gate.missing_fields:
        asks = ", ".join(FIELD_QUESTIONS.get(f, f) for f in gate.missing_fields)
        return f"Before I plan anything, could you tell me {asks}?"
    if gate.reason is GateReason.NO_PLAN_TO_MODIFY:
        return "There is no plan to change yet. Would you like me to plan a new trip first?"
    return "Could you say a bit more about what you would like: a new trip, a change to your plan, or a quick question?"


class ClarifyPath:
    def __init__(self, deps: RuntimeDeps) -> None:
        self.deps = deps

    async def run(
        self, state: SessionState, message: str, gate: GateResult, *, trace: TraceContext
    ) -> Clarification:
        gate_info = {
            "reason": gate.reason.value,
            "missing_fields": gate.missing_fields,
            "has_plan": state.plan is not None,
        }
        messages = [
            system(CLARIFY_SYSTEM, self.deps.canary),
            data_message(
                CLARIFY_INSTRUCTION,
                [
                    ("gate", json.dumps(gate_info)),
                    ("trip_context", state.context.model_dump_json()),
                    ("user_message", message),
                ],
            ),
        ]
        try:
            draft = await complete_structured(
                self.deps.agent_caller,
                purpose=Purpose.CLARIFY,
                messages=messages,
                schema=ClarificationDraft,
                trace=trace,
                path=PathName.CLARIFY,
                max_repairs=self.deps.settings.max_model_repairs,
            )
            text = draft.question
        except (ModelCallError, OutputRejected, StructuredOutputError):
            text = fallback_question(gate)
        if gate.missing_fields and not all(
            any(word in text.casefold() for word in _keywords(f)) for f in gate.missing_fields
        ):
            # The drafted question must ask for every missing key variable.
            text = f"{text} {fallback_question(gate)}"
        return Clarification(text=text, reason=gate.reason, missing_fields=gate.missing_fields)


def _keywords(field: str) -> tuple[str, ...]:
    return {
        "destination": ("where", "destination"),
        "dates": ("date", "when"),
        "party_size": ("how many", "people", "party", "travellers", "travelers"),
        "budget": ("budget",),
    }.get(field, (field,))
