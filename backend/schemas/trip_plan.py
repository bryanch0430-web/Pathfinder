"""TripPlan: the centralised state object (proposal §3.1) and the planner's output contract.

Every fact in a plan carries a `SourceRef` back to the tool call that produced it, so the
grounding metric can join generated entities to the tool log, and so the runtime can refuse
entities that do not resolve to a fetched record. Sections whose source could not be used are
marked `unavailable` and left empty instead of being filled from model knowledge.
"""

from __future__ import annotations

import datetime as _dt
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator

from backend.schemas.common import (
    AgentName,
    GeoPoint,
    Money,
    SectionStatus,
    StrictModel,
    ToolName,
    utcnow,
)

TRIP_PLAN_SCHEMA_VERSION = "1.0"


class SourceRef(StrictModel):
    tool: ToolName
    call_id: str
    provider: str
    fetched_at: datetime


class Place(StrictModel):
    place_id: str
    name: str
    category: str
    location: GeoPoint | None = None
    address: str | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    indoor: bool | None = None
    price: Money | None = Field(default=None, description="Per person admission")
    needs_reservation: bool = False
    closed_dates: list[date] = Field(default_factory=list)
    opening_hours: str | None = None
    source: SourceRef
    geocode_source: SourceRef | None = None


class Hotel(StrictModel):
    hotel_id: str
    name: str
    location: GeoPoint | None = None
    address: str | None = None
    nightly_price: Money
    rating: float | None = Field(default=None, ge=0, le=5)
    style: str | None = None
    distance_km: float | None = Field(default=None, ge=0, description="To the destination centre")
    source: SourceRef


class DailyForecast(StrictModel):
    date: date
    summary: str
    temp_min_c: float
    temp_max_c: float
    precipitation_chance: float = Field(ge=0, le=1)
    warning_signal: str | None = None
    source: SourceRef


class TicketOption(StrictModel):
    ticket_id: str
    direction: Literal["outbound", "return"]
    mode: Literal["train", "flight"]
    carrier: str
    origin: str
    destination: str
    depart_at: datetime
    arrive_at: datetime
    price: Money = Field(description="Per person")
    status: Literal["scheduled", "delayed", "cancelled"] = "scheduled"
    delay_minutes: int = Field(default=0, ge=0)
    confirmed: bool = False
    source: SourceRef


class Reservation(StrictModel):
    place_name: str
    place_id: str | None = None
    lead_time_days: int = Field(ge=0)
    booking_url: str | None = None
    source: SourceRef


class ItineraryItem(StrictModel):
    item_id: str
    place_id: str
    title: str
    start_time: time | None = None
    end_time: time | None = None
    confirmed: bool = Field(default=False, description="Confirmed items survive modify turns")
    needs_reservation: bool = False
    note: str | None = None


class DayPlan(StrictModel):
    date: date
    items: list[ItineraryItem] = Field(default_factory=list)
    forecast: DailyForecast | None = None


class HotelStay(StrictModel):
    hotel: Hotel
    check_in: date
    check_out: date
    confirmed: bool = False


class SectionState(StrictModel):
    agent: AgentName
    status: SectionStatus
    fetched_at: datetime | None = None
    reason: str | None = None
    tool_call_ids: list[str] = Field(default_factory=list)


class CheckName(StrEnum):
    """The check-and-merge checks named in Appendix A: dates, budget, route, tickets."""

    DATES = "dates"
    BUDGET = "budget"
    ROUTE = "route"
    TICKETS = "tickets"


class ConstraintViolation(StrictModel):
    check: CheckName
    message: str
    item_ids: list[str] = Field(default_factory=list)


class DisruptionKind(StrEnum):
    VENUE_CLOSED = "venue_closed"
    WEATHER_WARNING = "weather_warning"
    TRANSIT_DELAY = "transit_delay"


class Disruption(StrictModel):
    kind: DisruptionKind
    detail: str
    date: _dt.date | None = None
    affected_ids: list[str] = Field(default_factory=list)
    detected_at: datetime = Field(default_factory=utcnow)
    resolved: bool = False
    resolution: str | None = None


class CostBreakdown(StrictModel):
    currency: str = Field(min_length=3, max_length=3)
    hotel: float = Field(ge=0)
    tickets: float = Field(ge=0)
    attractions: float = Field(ge=0)
    total: float = Field(ge=0)
    complete: bool = Field(description="False when a priced section is unavailable")


class TripPlan(StrictModel):
    schema_version: Literal["1.0"] = TRIP_PLAN_SCHEMA_VERSION
    plan_id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    destination: str = Field(min_length=1)
    origin: str | None = None
    start_date: date
    end_date: date
    party_size: int = Field(ge=1)
    budget: Money | None = None
    days: list[DayPlan] = Field(min_length=1)
    places: list[Place] = Field(default_factory=list)
    hotel: HotelStay | None = None
    tickets: list[TicketOption] = Field(default_factory=list)
    reservations: list[Reservation] = Field(default_factory=list)
    sections: list[SectionState] = Field(default_factory=list)
    cost: CostBreakdown | None = None
    violations: list[ConstraintViolation] = Field(default_factory=list)
    disruptions: list[Disruption] = Field(default_factory=list)
    saved_trip_refs: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _structural_checks(self) -> TripPlan:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        expected = [
            self.start_date + timedelta(days=i)
            for i in range((self.end_date - self.start_date).days + 1)
        ]
        actual = [d.date for d in self.days]
        if actual != expected:
            raise ValueError(
                "days must list every date from start_date to end_date exactly once, in order"
            )
        place_ids = {p.place_id for p in self.places}
        if len(place_ids) != len(self.places):
            raise ValueError("place_id values must be unique")
        seen_items: set[str] = set()
        for day in self.days:
            if day.forecast is not None and day.forecast.date != day.date:
                raise ValueError(f"forecast date {day.forecast.date} does not match day {day.date}")
            for item in day.items:
                if item.item_id in seen_items:
                    raise ValueError(f"duplicate item_id {item.item_id}")
                seen_items.add(item.item_id)
                if item.place_id not in place_ids:
                    raise ValueError(f"item {item.item_id} references unknown place_id {item.place_id}")
                if item.start_time and item.end_time and item.end_time <= item.start_time:
                    raise ValueError(f"item {item.item_id} ends before it starts")
        agents = [s.agent for s in self.sections]
        if len(set(agents)) != len(agents):
            raise ValueError("at most one section state per agent")
        if self.hotel and self.hotel.check_out <= self.hotel.check_in:
            raise ValueError("hotel check_out must be after check_in")
        return self

    # ---- convenience accessors (pure; no mutation) ------------------------------------------
    def section(self, agent: AgentName) -> SectionState | None:
        return next((s for s in self.sections if s.agent is agent), None)

    def place(self, place_id: str) -> Place | None:
        return next((p for p in self.places if p.place_id == place_id), None)

    def all_items(self) -> list[ItineraryItem]:
        return [item for day in self.days for item in day.items]

    def day(self, on: date) -> DayPlan | None:
        return next((d for d in self.days if d.date == on), None)
