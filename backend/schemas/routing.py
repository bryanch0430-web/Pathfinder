"""System 1 router contracts.

Proposal: the router "does not generate replies immediately. It returns a typed intent ('plan',
'modify', or 'ask'), a clarity score and a decision on whether the request needs clarification."
`RouterDecision` is the only shape the router output may take; anything else is rejected.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import Field

from backend.schemas.common import AgentName, Money, Route, StrictModel


class RouterDecision(StrictModel):
    route: Route
    confidence: float = Field(ge=0, le=1, description="Route probability")
    clarity: float = Field(ge=0, le=1, description="How fully specified the request is")
    needs_clarification: bool
    affected_parts: list[AgentName] = Field(
        default_factory=list, description="For 'modify': the parts System 1 names as changed"
    )


class GateReason(StrEnum):
    ACCEPTED = "accepted"
    BELOW_THRESHOLD = "below_threshold"
    MODEL_FLAGGED = "model_flagged"  # router said needs_clarification
    UNCLEAR_ROUTE = "unclear_route"
    MISSING_FIELDS = "missing_fields"  # dates / party size / budget / destination missing
    UNTYPED_OUTPUT = "untyped_output"  # router emitted free text -> rejected
    NO_PLAN_TO_MODIFY = "no_plan_to_modify"


class GateResult(StrictModel):
    """Final routing outcome after the clarification gate. `route` is the path actually taken:
    anything not accepted becomes `Route.UNCLEAR` (clarify and return the turn to the user)."""

    route: Route
    reason: GateReason
    decision: RouterDecision | None = None
    missing_fields: list[str] = Field(default_factory=list)


class FocusKind(StrEnum):
    """The plan parts a chat message can be scoped to (plan workspace selection)."""

    DAY = "day"  # id = the day's ISO date
    ITEM = "item"  # id = ItineraryItem.item_id
    HOTEL = "hotel"  # id = Hotel.hotel_id of the plan's stay
    TICKET = "ticket"  # id = TicketOption.ticket_id


class PlanFocus(StrictModel):
    """The one part of the current plan a chat message is about."""

    kind: FocusKind
    id: str = Field(min_length=1, max_length=200)


class ChangeRequest(StrictModel):
    """Structured edit extracted from a modify message. Only set fields change."""

    start_date: date | None = None
    end_date: date | None = None
    party_size: int | None = Field(default=None, ge=1, le=50)
    budget: Money | None = None
    hotel_style: str | None = Field(default=None, max_length=100)
    replace_hotel: bool = False
    cheaper_hotel: bool = Field(
        default=False, description="The replacement hotel must cost less per night than the current one"
    )
    remove_place_ids: list[str] = Field(default_factory=list)
    add_requests: list[str] = Field(default_factory=list, description="e.g. 'a museum on day 2'")
    replace_focus: bool = Field(
        default=False, description="Focused turns only: swap the focused stop, hotel or ticket for another option"
    )
    refresh: list[AgentName] = Field(
        default_factory=list, description="Sections the user asked to re-check (e.g. weather)"
    )
    summary: str = Field(default="", max_length=500)
