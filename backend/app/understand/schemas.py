"""Structured answers the model must produce.

Pydantic here is the contract, not decoration: the LLM gateway repairs a reply
that does not validate, and the rest of the app only ever sees these objects.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.models import ImportedItinerary

Intent = Literal["plan", "modify", "ask", "unclear"]
MealSlot = Literal["breakfast", "lunch", "dinner", "snack"]


class Draft(BaseModel):
    model_config = ConfigDict(extra="ignore")


def _iso(value: Any) -> str | None:
    if value in (None, "", "null"):
        return None
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return None


class StopSlot(Draft):
    city: str
    nights: int | None = None


class SlotUpdate(Draft):
    destination: str | None = None
    origin: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    days: int | None = None
    party_size: int | None = None
    budget: float | None = None
    currency: str | None = None
    hotel_style: str | None = None
    preferences: list[str] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)
    pace: str | None = None
    with_children: bool | None = None
    purpose: str | None = None
    step_free: bool | None = None
    stops: list[StopSlot] | None = None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _dates(cls, value: Any) -> str | None:
        return _iso(value)


class Suggestion(Draft):
    slot: str = ""
    name: str = ""
    asked: str = ""


class ParsedIntent(Draft):
    """The turn analyst's whole answer."""

    intent: Intent = "unclear"
    confidence: float = 0.0
    reason_code: str = ""
    reason: str = ""
    language: str = "en"
    slots: SlotUpdate = Field(default_factory=SlotUpdate)
    clears: list[str] = Field(default_factory=list)
    remove_constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    affected_parts: list[str] = Field(default_factory=list)
    need_clarification: bool = False
    missing_slots: list[str] = Field(default_factory=list)
    question: str | None = None
    options: dict[str, list[str]] = Field(default_factory=dict)
    choices: list[dict[str, str]] = Field(default_factory=list)
    suggestions: list[Suggestion] = Field(default_factory=list)
    start_new_trip: bool = False
    wants_human: bool = False
    self_harm: bool = False
    frustration: float = 0.0
    injection_suspected: bool = False
    edit_summary: str = ""
    research_places: list[str] = Field(default_factory=list)
    search_queries: list[str] = Field(default_factory=list)

    @field_validator("intent", mode="before")
    @classmethod
    def _intent(cls, value: Any) -> str:
        text = str(value or "").strip().lower()
        aliases = {
            "clarify": "unclear", "clarification": "unclear", "question": "ask",
            "edit": "modify", "change": "modify", "create": "plan", "new": "plan",
            "planning": "plan", "unknown": "unclear", "other": "unclear",
        }
        if text in {"plan", "modify", "ask", "unclear"}:
            return text
        return aliases.get(text, "unclear")

    @field_validator("frustration", mode="before")
    @classmethod
    def _frustration(cls, value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        if number > 1:
            number = number / 100.0
        return max(0.0, min(1.0, number))

    @field_validator("confidence", mode="before")
    @classmethod
    def _confidence(cls, value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        if number > 1:
            number = number / 100.0
        return max(0.0, min(1.0, number))

    @field_validator("slots", mode="before")
    @classmethod
    def _slots(cls, value: Any) -> Any:
        return value or {}

    @field_validator("suggestions", mode="before")
    @classmethod
    def _suggestions(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return []
        out = []
        for row in value:
            if isinstance(row, str):
                out.append({"name": row})
            elif isinstance(row, dict):
                out.append(row)
        return out


class StopChoice(Draft):
    ref: str
    start: str = ""
    end: str = ""
    note: str = ""
    meal_slot: MealSlot | None = None

    @field_validator("start", "end", mode="before")
    @classmethod
    def _time(cls, value: Any) -> str:
        if value in (None, ""):
            return ""
        text = str(value).strip()
        parts = text.replace(".", ":").split(":")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"{int(parts[0]) % 24:02d}:{int(parts[1]) % 60:02d}"
        return text


class DayChoice(Draft):
    date: str
    title: str = ""
    note: str = ""
    stops: list[StopChoice] = Field(default_factory=list)

    @field_validator("date", mode="before")
    @classmethod
    def _date(cls, value: Any) -> str:
        return _iso(value) or ""


class ConstraintNote(Draft):
    text: str = ""
    met: bool = True
    evidence: str = ""


class PlanDraft(Draft):
    summary: str = ""
    days: list[DayChoice] = Field(default_factory=list)
    hotel_ref: str | None = None
    hotel_alt_refs: list[str] = Field(default_factory=list)
    ticket_refs: list[str] = Field(default_factory=list)
    constraint_notes: list[ConstraintNote] = Field(default_factory=list)
    cost_notes: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class CheckRow(Draft):
    text: str = ""
    status: Literal["pass", "fail", "unknown"] = "unknown"
    evidence: str = ""


class VerifyResult(Draft):
    checks: list[CheckRow] = Field(default_factory=list)
    problems: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class EditOp(Draft):
    op: Literal[
        "move",
        "retime",
        "remove",
        "add",
        "replace",
        "swap_hotel",
        "swap_ticket",
        "set_field",
        "note",
    ]
    item_id: str | None = None
    day: int | None = None
    start: str | None = None
    end: str | None = None
    ref: str | None = None
    name: str | None = None
    note: str | None = None
    text: str | None = None
    field: str | None = None
    value: str | float | int | None = None

    @field_validator("start", "end", mode="before")
    @classmethod
    def _time(cls, value: Any) -> str | None:
        if value in (None, ""):
            return None
        text = str(value).strip()
        parts = text.replace(".", ":").split(":")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            return f"{int(parts[0]) % 24:02d}:{int(parts[1]) % 60:02d}"
        return text


class EditDraft(Draft):
    scope: Literal["stop", "day", "whole"] = "stop"
    ops: list[EditOp] = Field(default_factory=list)
    summary: str = ""
    detail: str = ""
    needs_research: bool = False
    research_kind: Literal["attraction", "hotel", "meal", "ticket"] = "attraction"
    research_name: str = ""
    research_queries: list[str] = Field(default_factory=list)
    rebuild_reason: str = ""


class IntakeNote(Draft):
    name: str = ""
    note: str = ""
    readable: bool = True


class IntakeFacts(Draft):
    destination: str | None = None
    origin: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    days: int | None = None
    party_size: int | None = None
    budget: float | None = None
    currency: str | None = None
    preferences: list[str] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)
    places: list[str] = Field(default_factory=list)
    notes: list[IntakeNote] = Field(default_factory=list)
    itinerary: ImportedItinerary | None = None

    @field_validator("start_date", "end_date", mode="before")
    @classmethod
    def _dates(cls, value: Any) -> str | None:
        return _iso(value)


class TranslationDraft(Draft):
    summary: str | None = None
    days: dict[str, dict[str, str]] = Field(default_factory=dict)
    items: dict[str, dict[str, str]] = Field(default_factory=dict)
    gaps: dict[str, str] = Field(default_factory=dict)
