"""Typed tool requests, tool payloads and the uniform `ToolOutcome` envelope.

Why strict request models: proposal §4 says "Invalid JSON, a missing field, or a date written as
free text is not sent again unchanged. An adapter returns a structured validation error, and the
model may repair it at most twice." Strict Pydantic models (extra="forbid", ISO dates) are what
turn a malformed model-proposed call into a structured list of `FieldIssue`s.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from backend.schemas.common import GeoPoint, Money, StrictModel, ToolName


class ToolOperation(StrEnum):
    WEB_SEARCH = "web_search"
    GEOCODE = "geocode"
    FORECAST = "forecast"
    PLACES_SEARCH = "places_search"
    TICKET_SEARCH = "ticket_search"
    RESERVATION_CHECK = "reservation_check"


OPERATION_TOOL: dict[ToolOperation, ToolName] = {
    ToolOperation.WEB_SEARCH: ToolName.WEB_SEARCH,
    ToolOperation.GEOCODE: ToolName.MAPS,
    ToolOperation.FORECAST: ToolName.WEATHER,
    ToolOperation.PLACES_SEARCH: ToolName.PLACES,
    ToolOperation.TICKET_SEARCH: ToolName.TICKETS,
    ToolOperation.RESERVATION_CHECK: ToolName.TICKETS,
}

MAX_FORECAST_SPAN_DAYS = 16


# ------------------------------------------------------------------------------------------------
# Requests (what a model proposes; discriminated by `operation`)
# ------------------------------------------------------------------------------------------------


class WebSearchRequest(StrictModel):
    operation: Literal[ToolOperation.WEB_SEARCH] = ToolOperation.WEB_SEARCH
    query: str = Field(min_length=2, max_length=300)
    destination: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=8, ge=1, le=20)


class GeocodeRequest(StrictModel):
    operation: Literal[ToolOperation.GEOCODE] = ToolOperation.GEOCODE
    query: str = Field(min_length=1, max_length=300)
    city: str = Field(min_length=1, max_length=200)


class ForecastRequest(StrictModel):
    operation: Literal[ToolOperation.FORECAST] = ToolOperation.FORECAST
    location: str = Field(min_length=1, max_length=200)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _check_span(self) -> ForecastRequest:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        if (self.end_date - self.start_date).days + 1 > MAX_FORECAST_SPAN_DAYS:
            raise ValueError(f"forecast span must be at most {MAX_FORECAST_SPAN_DAYS} days")
        return self


class PlacesSearchRequest(StrictModel):
    operation: Literal[ToolOperation.PLACES_SEARCH] = ToolOperation.PLACES_SEARCH
    destination: str = Field(min_length=1, max_length=200)
    category: Literal["hotel", "attraction"]
    max_price: float | None = Field(default=None, gt=0, description="Nightly (hotel) cap")
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    style: str | None = Field(default=None, max_length=100)
    limit: int = Field(default=10, ge=1, le=30)


class TicketSearchRequest(StrictModel):
    operation: Literal[ToolOperation.TICKET_SEARCH] = ToolOperation.TICKET_SEARCH
    origin: str = Field(min_length=1, max_length=200)
    destination: str = Field(min_length=1, max_length=200)
    travel_date: date
    modes: list[Literal["train", "flight"]] = Field(min_length=1)
    party_size: int = Field(default=1, ge=1, le=50)


class ReservationCheckRequest(StrictModel):
    operation: Literal[ToolOperation.RESERVATION_CHECK] = ToolOperation.RESERVATION_CHECK
    destination: str = Field(min_length=1, max_length=200)
    place_names: list[str] = Field(default_factory=list, max_length=50)


ToolRequest = Annotated[
    WebSearchRequest
    | GeocodeRequest
    | ForecastRequest
    | PlacesSearchRequest
    | TicketSearchRequest
    | ReservationCheckRequest,
    Field(discriminator="operation"),
]


def tool_for(request: ToolRequest) -> ToolName:
    return OPERATION_TOOL[request.operation]


# ------------------------------------------------------------------------------------------------
# Provider records (what a source returns, before agents attach provenance)
# ------------------------------------------------------------------------------------------------


class SearchHit(StrictModel):
    title: str
    url: str
    snippet: str


class GeocodeResult(StrictModel):
    query: str
    location: GeoPoint
    formatted_address: str


class ForecastDay(StrictModel):
    date: date
    summary: str
    temp_min_c: float
    temp_max_c: float
    precipitation_chance: float = Field(ge=0, le=1)
    warning_signal: str | None = Field(
        default=None, description="Official weather warning, e.g. Hong Kong typhoon signal 'T8'"
    )


class PlaceRecord(StrictModel):
    place_id: str
    name: str
    category: str
    location: GeoPoint | None = None
    address: str | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    indoor: bool | None = None
    price: Money | None = Field(default=None, description="Per person (attraction) or per night (hotel)")
    style: str | None = None
    needs_reservation: bool = False
    closed_dates: list[date] = Field(default_factory=list)
    opening_hours: str | None = None


class TicketRecord(StrictModel):
    ticket_id: str
    mode: Literal["train", "flight"]
    carrier: str
    origin: str
    destination: str
    depart_at: datetime
    arrive_at: datetime
    price: Money = Field(description="Per person")
    status: Literal["scheduled", "delayed", "cancelled"] = "scheduled"
    delay_minutes: int = Field(default=0, ge=0)


class ReservationRecord(StrictModel):
    place_name: str
    lead_time_days: int = Field(ge=0)
    booking_url: str | None = None


class SearchPayload(StrictModel):
    kind: Literal["search"] = "search"
    hits: list[SearchHit]


class GeocodePayload(StrictModel):
    kind: Literal["geocode"] = "geocode"
    result: GeocodeResult


class ForecastPayload(StrictModel):
    kind: Literal["forecast"] = "forecast"
    days: list[ForecastDay]


class PlacesPayload(StrictModel):
    kind: Literal["places"] = "places"
    places: list[PlaceRecord]


class TicketsPayload(StrictModel):
    kind: Literal["tickets"] = "tickets"
    tickets: list[TicketRecord]


class ReservationsPayload(StrictModel):
    kind: Literal["reservations"] = "reservations"
    reservations: list[ReservationRecord]


ToolPayload = Annotated[
    SearchPayload
    | GeocodePayload
    | ForecastPayload
    | PlacesPayload
    | TicketsPayload
    | ReservationsPayload,
    Field(discriminator="kind"),
]


# ------------------------------------------------------------------------------------------------
# Outcome envelope
# ------------------------------------------------------------------------------------------------


class ToolOutcomeStatus(StrEnum):
    OK = "ok"
    VALIDATION_ERROR = "validation_error"
    UNAVAILABLE = "unavailable"
    BLOCKED = "blocked"


class FailureKind(StrEnum):
    """Failure classification done BEFORE any retry (proposal §4, data validation)."""

    VALIDATION = "validation"  # malformed request -> structured error -> model repair (never resent unchanged)
    TIMEOUT = "timeout"  # transient -> retry with backoff + jitter inside the time budget
    RATE_LIMIT = "rate_limit"  # transient -> retry with backoff + jitter inside the time budget
    SERVER_FAULT = "server_fault"  # transient -> retry with backoff + jitter inside the time budget
    PERMANENT = "permanent"  # source cannot be used (auth, not found, provider down) -> unavailable
    NOT_ALLOWED = "not_allowed"  # tool not on the path allowlist -> blocked and logged
    BUDGET_EXHAUSTED = "budget_exhausted"  # time budget ran out -> unavailable


TRANSIENT_FAILURES: frozenset[FailureKind] = frozenset(
    {FailureKind.TIMEOUT, FailureKind.RATE_LIMIT, FailureKind.SERVER_FAULT}
)


class FieldIssue(StrictModel):
    loc: str = Field(description="Dotted path of the offending field, '' for the whole payload")
    message: str
    kind: str = Field(description="Machine-readable error type, e.g. 'date_from_datetime_parsing'")


class ToolOutcome(StrictModel):
    call_id: str
    operation: ToolOperation | None = None
    tool: ToolName | None = None
    status: ToolOutcomeStatus
    request: ToolRequest | None = None
    payload: ToolPayload | None = None
    issues: list[FieldIssue] = Field(default_factory=list)
    failure: FailureKind | None = None
    attempts: int = 0
    provider: str | None = None
    fetched_at: datetime | None = Field(default=None, description="Set on success; drives staleness")
    latency_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status is ToolOutcomeStatus.OK
