"""Inputs and outputs of the four pre-planning agents and the planner."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field

from backend.schemas.common import AgentName, SectionStatus, StrictModel
from backend.schemas.memory import PreferenceProfile, SimilarTrip
from backend.schemas.trip import TripContext
from backend.schemas.trip_plan import (
    DailyForecast,
    Disruption,
    Hotel,
    Place,
    Reservation,
    TicketOption,
    TripPlan,
)


class AgentTask(StrictModel):
    """What one agent is asked to do this turn. Hints narrow a modify-path re-run."""

    context: TripContext
    preferences: PreferenceProfile = Field(default_factory=PreferenceProfile)
    exclude_ids: list[str] = Field(
        default_factory=list, description="Hotel/place/ticket ids that must not be returned"
    )
    indoor_only_dates: list[date] = Field(
        default_factory=list, description="Dates with a weather warning: prefer indoor places"
    )
    notes: list[str] = Field(default_factory=list, description="Free-text change requests")


class AttractionData(StrictModel):
    kind: Literal["attraction"] = "attraction"
    places: list[Place]


class HotelData(StrictModel):
    kind: Literal["hotel"] = "hotel"
    candidates: list[Hotel] = Field(description="Ranked best first (rating and distance)")


class WeatherData(StrictModel):
    kind: Literal["weather"] = "weather"
    forecasts: list[DailyForecast]


class TicketData(StrictModel):
    kind: Literal["ticket"] = "ticket"
    outbound: list[TicketOption]
    inbound: list[TicketOption]
    reservations: list[Reservation]


AgentData = Annotated[
    AttractionData | HotelData | WeatherData | TicketData, Field(discriminator="kind")
]


class AgentResult(StrictModel):
    agent: AgentName
    status: SectionStatus
    reason: str | None = None
    data: AgentData | None = None
    fetched_at: datetime | None = None
    tool_call_ids: list[str] = Field(default_factory=list)
    disruptions: list[Disruption] = Field(default_factory=list)


class SavedTripHint(StrictModel):
    """A saved trip above the similarity cutoff, reduced to what the planner may reuse."""

    trip_id: str
    destination: str
    rating: int
    similarity: float
    place_names: list[str]
    hotel_name: str | None = None

    @classmethod
    def from_similar(cls, trip: SimilarTrip) -> SavedTripHint:
        return cls(
            trip_id=trip.trip_id,
            destination=trip.destination,
            rating=trip.rating,
            similarity=trip.similarity,
            place_names=[p.name for p in trip.plan.places],
            hotel_name=trip.plan.hotel.hotel.name if trip.plan.hotel else None,
        )


class PlannerInput(StrictModel):
    plan_id: str
    context: TripContext
    preferences: PreferenceProfile
    results: list[AgentResult]
    saved_trips: list[SavedTripHint] = Field(default_factory=list)
    previous_plan: TripPlan | None = None
