"""Key trip variables gathered by the web console.

Proposal: "the web console explicitly gathers the key trip variables, including days, party
size, location, the current plan, and hard constraints such as budget." Missing key variables
are never guessed: `TripContext.missing_key_fields()` drives the clarification gate.
"""

from __future__ import annotations

from datetime import date, timedelta
from enum import StrEnum

from pydantic import Field, model_validator

from backend.schemas.common import Money, StrictModel


class ConstraintKind(StrEnum):
    """Hard-constraint classes; also the stratification key for the 70/30 eval split."""

    BUDGET = "budget"
    MUST_VISIT = "must_visit"
    AVOID = "avoid"
    DATES = "dates"
    PARTY = "party"
    FREE_TEXT = "free_text"


class HardConstraint(StrictModel):
    kind: ConstraintKind
    value: str = Field(min_length=1, max_length=500, description="Place name, category or text")


class TripContext(StrictModel):
    destination: str | None = Field(default=None, max_length=200)
    origin: str | None = Field(
        default=None, max_length=200, description="Departure city for train/flight tickets"
    )
    start_date: date | None = None
    end_date: date | None = None
    days: int | None = Field(default=None, ge=1, le=60)
    party_size: int | None = Field(default=None, ge=1, le=50)
    budget: Money | None = None
    hotel_style: str | None = Field(default=None, max_length=100)
    hard_constraints: list[HardConstraint] = Field(default_factory=list)

    @model_validator(mode="after")
    def _derive_dates(self) -> TripContext:
        if self.start_date and self.days and not self.end_date:
            self.end_date = self.start_date + timedelta(days=self.days - 1)
        if self.start_date and self.end_date:
            if self.end_date < self.start_date:
                raise ValueError("end_date must not be before start_date")
            self.days = (self.end_date - self.start_date).days + 1
        return self

    def missing_key_fields(self) -> list[str]:
        """Key variables the planner must not guess (proposal §3.2, second challenge)."""
        missing: list[str] = []
        if not self.destination:
            missing.append("destination")
        if not (self.start_date and self.end_date):
            missing.append("dates")
        if not self.party_size:
            missing.append("party_size")
        if not self.budget:
            missing.append("budget")
        return missing

    def date_range(self) -> list[date]:
        if not (self.start_date and self.end_date):
            return []
        span = (self.end_date - self.start_date).days
        return [self.start_date + timedelta(days=i) for i in range(span + 1)]
