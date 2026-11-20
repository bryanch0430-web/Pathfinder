"""The trip document: one normalized trip that the studio, sketchbook and every export read.

It is the same shape for both planning modes, so views and exports never disagree.
"""

import datetime as dt
from datetime import date
from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class DocItem(BaseModel):
    name: str
    category: str = "other"
    time_of_day: Optional[Literal["morning", "afternoon", "evening"]] = None
    start: Optional[str] = None  # HH:MM planning slot, when the itinerary stated one
    end: Optional[str] = None
    area: Optional[str] = None
    est_cost: Optional[float] = None  # per person, trip currency (build-with-the-agent estimates)
    duration_hours: Optional[float] = None
    tip: Optional[str] = None  # the traveler insight behind the suggestion
    source_url: Optional[str] = None
    hours: Optional[str] = None  # listed opening hours, when a source gave them
    option: bool = False  # one of several alternatives the plan offers for that day


class DocDay(BaseModel):
    day: int
    date: Optional[dt.date] = None  # dt.date: a bare "date" here would resolve to the field itself
    base: str
    items: list[DocItem] = Field(default_factory=list)
    est_cost: Optional[float] = None  # whole party, from the agent's day total
    hours: Optional[float] = None
    travel_hours: Optional[float] = None
    travel_distance_km: Optional[float] = None
    travel_basis: Optional[str] = None
    unverified_legs: int = 0


class DocRouteWarning(BaseModel):
    day: int
    severity: Literal["impossible", "tight", "unverified"]
    message: str
    travel_hours: float = 0
    distance_km: float = 0
    basis: str = "distance estimate"


class DocLeg(BaseModel):
    source: str
    target: str
    mode: Optional[str] = None
    duration: Optional[str] = None
    distance: Optional[str] = None
    cost: Optional[str] = None


class DocBudget(BaseModel):
    currency: str
    target: Optional[float] = None
    planned: Optional[float] = None  # canonical ledger projection for planned trips
    entered: float = 0.0  # amounts the traveler entered or accepted in the budget planner
    projected: float = 0.0  # entered + suggestions still pending
    is_locked: bool = False
    locked_total: Optional[float] = None


class DocTravelers(BaseModel):
    adults: int = 1
    children: int = 0


class DocNote(BaseModel):
    tone: Literal["good", "warn", "info"]
    text: str


class DocWeather(BaseModel):
    day: int
    date: dt.date
    base: str
    kind: Literal["forecast", "typical"]  # a real forecast, or the month's climate average
    label: str  # "Forecast" or "Typical for October": always shown next to the numbers
    condition: Optional[Literal["sun", "partly", "cloud", "fog", "rain", "snow", "storm"]] = None
    temp_min: Optional[float] = None
    temp_max: Optional[float] = None
    temp_mean: Optional[float] = None
    rain_mm: Optional[float] = None  # forecast: that day's total; typical: average per day
    badge: Optional[DocNote] = None
    notes: list[DocNote] = Field(default_factory=list)


class DocHoliday(BaseModel):
    day: int
    date: dt.date
    name: str
    local_name: Optional[str] = None
    country: str  # ISO 3166-1 alpha-2
    regional: bool = False  # only some regions observe it


class DocExchange(BaseModel):
    base: str  # the trip currency
    rates: dict[str, float]  # 1 base = rate × currency
    date: str  # reference-rate date


class DocSource(BaseModel):
    name: str
    url: str
    covers: str  # what it provided, e.g. "Weather forecast"


class DocPlace(BaseModel):
    name: str  # a day's base city, as written in the plan
    lat: float
    lon: float
    country: Optional[str] = None  # ISO 3166-1 alpha-2, lower case


class DocEnrichment(BaseModel):
    places: list[DocPlace] = Field(default_factory=list)  # located base cities (OpenStreetMap)
    weather: list[DocWeather] = Field(default_factory=list)
    holidays: list[DocHoliday] = Field(default_factory=list)
    exchange: Optional[DocExchange] = None
    sources: list[DocSource] = Field(default_factory=list)  # attribution for everything above


class TripDocument(BaseModel):
    trip_id: UUID
    mode: Literal["agent", "one_shot", "none"]
    is_final: bool = False
    status: Optional[Literal["building", "complete"]] = None  # build-with-the-agent progress
    destination: Optional[str] = None
    origin: Optional[str] = None
    duration_days: Optional[int] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    travelers: DocTravelers = Field(default_factory=DocTravelers)
    comfort: str = "mid_range"
    travel_mode: str = "transit"
    pace: str = "balanced"
    interests: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    must_see: list[str] = Field(default_factory=list)
    guide: Optional[str] = None
    days: list[DocDay] = Field(default_factory=list)
    route_warnings: list[DocRouteWarning] = Field(default_factory=list)
    legs: list[DocLeg] = Field(default_factory=list)
    budget: DocBudget
    graph: Optional[dict] = None  # the itinerary graph the map and 3D views render
    enrichment: Optional[DocEnrichment] = None  # None when every source failed or it is switched off
