"""Prompt-injection defence contracts. Aggregate audit records hold NO message text and NO
session or user identifiers (proposal: "Suspected injections are logged in aggregate, without
personal data")."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from backend.schemas.common import PathName, StrictModel


class InjectionCategory(StrEnum):
    PROMPT_OVERRIDE = "prompt_override"
    HIDDEN_INSTRUCTION_REQUEST = "hidden_instruction_request"
    SMUGGLED_TOOL_CALL = "smuggled_tool_call"
    ROUTE_OVERRIDE = "route_override"


class ScreenResult(StrictModel):
    flagged: bool
    categories: list[InjectionCategory] = Field(default_factory=list)


class SecurityEventKind(StrEnum):
    INPUT_FLAGGED = "input_flagged"
    UNTYPED_ROUTE_REJECTED = "untyped_route_rejected"
    TOOL_BLOCKED = "tool_blocked"
    CANARY_LEAK = "canary_leak"


class AuditBucket(StrictModel):
    kind: SecurityEventKind
    category: InjectionCategory | None = None
    path: PathName | None = None
    day: str = Field(description="UTC date bucket, YYYY-MM-DD")
    count: int = Field(ge=0)


class AuditSnapshot(StrictModel):
    since: datetime
    buckets: list[AuditBucket] = Field(default_factory=list)

    def total(self, kind: SecurityEventKind) -> int:
        return sum(b.count for b in self.buckets if b.kind is kind)
