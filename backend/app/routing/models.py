"""Typed router output. The LangGraph trip is read as a small snapshot, not the other app's domain."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any


class Intent(str, Enum):
    PLAN = "plan"
    MODIFY = "modify"
    ASK = "ask"
    UNCLEAR = "unclear"


@dataclass
class TripSnapshot:
    destination: str | None = None
    origin: str | None = None
    duration_days: int | None = None
    start_date: str | None = None
    currency: str | None = None
    has_plan: bool = False

    def missing_slots(self) -> list[str]:
        missing: list[str] = []
        if not (self.destination or "").strip():
            missing.append("destination")
        if not (self.origin or "").strip():
            missing.append("origin")
        if not self.duration_days:
            missing.append("duration")
        return missing


@dataclass
class RouteDecision:
    intent: Intent
    confidence: float
    need_clarification: bool
    reason_code: str
    affected_parts: list[str] = field(default_factory=list)
    missing_slots: list[str] = field(default_factory=list)
    related_to_plan: bool = False
    rationale: str = ""
    source: str = "rules"

    def replace(self, **kwargs: Any) -> "RouteDecision":
        return replace(self, **kwargs)

    def public(self) -> dict[str, Any]:
        return {
            "intent": self.intent.value,
            "confidence": round(float(self.confidence), 3),
            "source": self.source,
            "reason": self.reason_code,
            "affected_parts": list(self.affected_parts),
            "need_clarification": self.need_clarification,
            "minimal": self.intent == Intent.MODIFY and not self.need_clarification,
        }
