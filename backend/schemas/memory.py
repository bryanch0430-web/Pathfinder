"""Session memory and long-term memory records."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from backend.schemas.common import Money, Route, StrictModel, utcnow
from backend.schemas.trip import TripContext
from backend.schemas.trip_plan import TripPlan


class ConversationTurn(StrictModel):
    role: Literal["user", "assistant"]
    content: str
    route: Route | None = None
    at: datetime = Field(default_factory=utcnow)


class PreferenceProfile(StrictModel):
    """What the user liked, kept as session memory and persisted by the background writer."""

    hotel_style: str | None = None
    liked_categories: list[str] = Field(default_factory=list)
    avoided_categories: list[str] = Field(default_factory=list)
    budget_hint: Money | None = None
    party_size_hint: int | None = Field(default=None, ge=1)


class SessionState(StrictModel):
    """Everything the router and planner need across turns (proposal §3.1 'centralised state')."""

    session_id: str
    context: TripContext = Field(default_factory=TripContext)
    plan: TripPlan | None = None
    history: list[ConversationTurn] = Field(default_factory=list)
    preferences: PreferenceProfile = Field(default_factory=PreferenceProfile)
    turn_count: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class SavedTripRecord(StrictModel):
    """A plan the user marked useful: saved with destination and rating (proposal §4)."""

    trip_id: str
    destination: str
    rating: int = Field(ge=1, le=5)
    summary: str
    plan: TripPlan
    created_at: datetime = Field(default_factory=utcnow)


class SimilarityHit(StrictModel):
    trip_id: str
    similarity: float = Field(ge=-1, le=1)


class SimilarTrip(StrictModel):
    trip_id: str
    destination: str
    rating: int = Field(ge=1, le=5)
    similarity: float = Field(ge=-1, le=1)
    summary: str
    plan: TripPlan
