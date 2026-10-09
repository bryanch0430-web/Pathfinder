"""Manual plan edits (plan workspace): the request body for editing one stop by hand."""

from __future__ import annotations

from datetime import date, time

from pydantic import Field, model_validator
from pydantic.json_schema import SkipJsonSchema

from backend.schemas.common import StrictModel


class PlanItemPatch(StrictModel):
    """Body of PATCH /api/sessions/{session_id}/plan/items/{item_id}. Only the fields sent
    change: `note: null` clears the note; times and day cannot be null. At least one field."""

    # SkipJsonSchema keeps null out of the contract for the fields that refuse it.
    start_time: time | SkipJsonSchema[None] = Field(default=None, description="Local time, HH:MM")
    end_time: time | SkipJsonSchema[None] = Field(default=None, description="Local time, HH:MM")
    note: str | None = Field(default=None, max_length=500)
    day: date | SkipJsonSchema[None] = Field(
        default=None, description="Move the stop to this trip date"
    )

    @model_validator(mode="after")
    def _check_fields(self) -> PlanItemPatch:
        sent = self.model_fields_set
        if not sent:
            raise ValueError("send at least one of start_time, end_time, note, day")
        for name in ("start_time", "end_time", "day"):
            if name in sent and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        for value in (self.start_time, self.end_time):
            if value is not None and value.tzinfo is not None:
                raise ValueError("times are local: send HH:MM without a timezone")
        return self
