"""Structured model output with bounded repair.

Proposal: "The parser checks the JSON against the schema and may repair it a bounded number of
times. If the TripPlan JSON still fails the schema after those repairs, the console does not
return it as a valid plan." The same bounded loop is used for every structured model output.
Repairs are explicit extra turns (the invalid output and the structured errors are shown to the
model); nothing is patched silently.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from backend.agents.runtime.caller import ModelCaller
from backend.schemas.common import AgentName, PathName
from backend.schemas.llm import ChatMessage, Role
from backend.schemas.observability import TraceContext
from backend.schemas.tools import FieldIssue

ModelT = TypeVar("ModelT", bound=BaseModel)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n(?P<body>.*)\n\s*```\s*$", re.DOTALL)


class StructuredOutputError(Exception):
    def __init__(self, purpose: str, issues: list[FieldIssue], attempts: int, last_raw: str) -> None:
        super().__init__(f"{purpose}: output failed schema after {attempts} attempt(s)")
        self.purpose = purpose
        self.issues = issues
        self.attempts = attempts
        self.last_raw = last_raw


def strip_json_fence(raw: str) -> str:
    """Accept one surrounding ```json fence (a formatting habit, not content). The router does
    NOT use this: its output must be bare typed JSON (see security.routes)."""
    match = _FENCE_RE.match(raw)
    return match.group("body") if match else raw.strip()


def issues_from_validation(error: ValidationError) -> list[FieldIssue]:
    return [
        FieldIssue(
            loc=".".join(str(part) for part in err["loc"]),
            message=err["msg"],
            kind=err["type"],
        )
        for err in error.errors(include_url=False)
    ]


def validate_output(raw: str, schema: type[ModelT]) -> tuple[ModelT | None, list[FieldIssue]]:
    text = strip_json_fence(raw)
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [FieldIssue(loc="", message=f"invalid JSON: {exc.msg}", kind="json_invalid")]
    try:
        return schema.model_validate_json(text), []
    except ValidationError as exc:
        return None, issues_from_validation(exc)


def repair_message(issues: Sequence[FieldIssue], *, what: str) -> ChatMessage:
    lines = "\n".join(f"- {i.loc or '(root)'}: {i.message} [{i.kind}]" for i in issues)
    return ChatMessage(
        role=Role.USER,
        content=(
            f"Your previous {what} failed validation with these structured errors:\n{lines}\n"
            "Return a corrected version as a single JSON object only. Change only what the errors "
            "require; do not add facts that are not in the provided data."
        ),
    )


async def complete_structured(
    caller: ModelCaller,
    *,
    purpose: str,
    messages: Sequence[ChatMessage],
    schema: type[ModelT],
    trace: TraceContext,
    path: PathName,
    max_repairs: int,
    agent: AgentName | None = None,
    max_tokens: int = 2048,
) -> ModelT:
    """Call the model, validate against `schema`, and repair at most `max_repairs` times."""
    response = await caller.call(
        purpose=purpose, messages=messages, trace=trace, path=path, agent=agent, max_tokens=max_tokens
    )
    raw = response.content
    attempts = 1
    while True:
        parsed, issues = validate_output(raw, schema)
        if parsed is not None:
            return parsed
        if attempts > max_repairs:
            raise StructuredOutputError(purpose, issues, attempts, raw)
        repair_turn = [
            *messages,
            ChatMessage(role=Role.ASSISTANT, content=raw),
            repair_message(issues, what="output"),
        ]
        response = await caller.call(
            purpose=f"{purpose}.repair",
            messages=repair_turn,
            trace=trace,
            path=path,
            agent=agent,
            max_tokens=max_tokens,
        )
        raw = response.content
        attempts += 1
