"""Enums and small value types shared by every layer.

These enums are the vocabulary of the typed contracts: the router may only emit a `Route`,
agents are addressed by `AgentName`, and the per-path tool allowlist is keyed by `PathName`
and `ToolName`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Route(StrEnum):
    """The only outputs the System 1 router may produce (proposal: typed intent)."""

    PLAN = "plan"
    MODIFY = "modify"
    ASK = "ask"
    UNCLEAR = "unclear"


class AgentName(StrEnum):
    """The four pre-planning agents. Each owns one section of the TripPlan."""

    ATTRACTION = "attraction"
    HOTEL = "hotel"
    WEATHER = "weather"
    TICKET = "ticket"


ALL_AGENTS: tuple[AgentName, ...] = (
    AgentName.ATTRACTION,
    AgentName.HOTEL,
    AgentName.WEATHER,
    AgentName.TICKET,
)


class ToolName(StrEnum):
    """Tool families from Appendix A ("Tools the steps call")."""

    WEB_SEARCH = "web_search"
    MAPS = "maps"
    WEATHER = "weather"
    PLACES = "places"
    TICKETS = "tickets"


class PathName(StrEnum):
    """Execution paths. Each has its own tool allowlist."""

    ROUTER = "router"
    PLAN = "plan"
    MODIFY = "modify"
    ASK = "ask"
    CLARIFY = "clarify"


class SectionStatus(StrEnum):
    """State of one agent-owned section of a plan.

    `unavailable` means the source could not be used; the section is left empty rather than
    filled from model knowledge. `stale` means the data is older than the configured limit and
    is marked for refresh on the next modify turn.
    """

    OK = "ok"
    UNAVAILABLE = "unavailable"
    STALE = "stale"


class StrictModel(BaseModel):
    """Base for contract models: unknown fields are rejected, not silently dropped."""

    model_config = ConfigDict(extra="forbid")


class Money(StrictModel):
    amount: float = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3, description="ISO 4217 code")


class GeoPoint(StrictModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


def utcnow() -> datetime:
    return datetime.now(UTC)
