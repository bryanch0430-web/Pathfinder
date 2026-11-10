"""Domain model for Pathfinder.

The domain owns the vocabulary of the product: a trip, its days, items,
sources, freshness and constraint checks. It deliberately has no knowledge of
FastAPI, HTTP, SQLite or any model vendor.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, model_serializer, model_validator

TimeStr = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str = "id") -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Intent(StrEnum):
    PLAN = "plan"
    MODIFY = "modify"
    ASK = "ask"
    UNCLEAR = "unclear"
    #: show the plan in another display language; never edits the plan
    SET_LANGUAGE = "set_language"
    #: a display language that is not supported (fixed reply)
    UNSUPPORTED = "unsupported"
    #: re-fetch the outdated sections through the freshness check, nothing else
    REFRESH = "refresh"


class DataStatus(StrEnum):
    OK = "ok"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class ItemType(StrEnum):
    ATTRACTION = "attraction"
    HOTEL = "hotel"
    MEAL = "meal"
    TRANSIT = "transit"
    TICKET = "ticket"


class AgentName(StrEnum):
    ATTRACTION = "attraction"
    HOTEL = "hotel"
    WEATHER = "weather"
    TICKET = "ticket"


class AgentStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    FAILED = "failed"


class FailureKind(StrEnum):
    NO_DATA = "no_data"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    SERVER_ERROR = "server_error"
    INVALID_REQUEST = "invalid_request"
    AUTH = "auth"
    BLOCKED = "blocked"
    NETWORK = "network"
    PARSE = "parse"


class PriceKind(StrEnum):
    LIVE = "live"
    REFERENCE = "reference"
    ESTIMATE = "estimate"
    NONE = "none"


class ConstraintStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class GeoPoint(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class Money(BaseModel):
    amount: float = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def _upper(self) -> "Money":
        self.currency = self.currency.upper()
        return self


class DateRange(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def _order(self) -> "DateRange":
        if self.end < self.start:
            raise ValueError("trip end date must not precede start date")
        if (self.end - self.start).days > 60:
            raise ValueError("trip is too long")
        return self

    @property
    def night_count(self) -> int:
        return max((self.end - self.start).days, 0)

    @property
    def dates(self) -> list[date]:
        return [date.fromordinal(self.start.toordinal() + i) for i in range((self.end - self.start).days + 1)]


class SourceRef(BaseModel):
    title: str
    url: str = ""
    source: str
    fetched_at: datetime = Field(default_factory=utcnow)
    published_at: datetime | None = None


class WeatherDay(BaseModel):
    date: date
    status: DataStatus = DataStatus.OK
    summary: str = ""
    high_c: float | None = None
    low_c: float | None = None
    precipitation_probability: int | None = Field(default=None, ge=0, le=100)
    source: str = ""
    fetched_at: datetime = Field(default_factory=utcnow)
    note: str = ""

    @property
    def is_available(self) -> bool:
        return self.status == DataStatus.OK and self.high_c is not None


class TransferLeg(BaseModel):
    """The trip from the previous stop, as a routes tool returned it (Proposal TC-09)."""

    from_item_id: str
    minutes: int = Field(ge=0)
    mode: str
    distance_km: float | None = None
    # Provider name ("transport-model"), the Toolbox tool that returned the leg
    # ("routes.segments", empty when the leg was not logged) and the leg's id in
    # that tool's result.
    source: str = ""
    tool: str = ""
    result_id: str = ""
    # Extra minutes allowed for a reported rail delay that day (Hong Kong MTR), on
    # top of ``minutes``. ``delay_tool`` is the status tool that reported it
    # ("routes.status"); ``delay_source`` is "assumed" when the feed gave no length.
    delay_minutes: int = Field(default=0, ge=0)
    delay_tool: str = ""
    delay_source: str = ""


MealSlot = Literal["breakfast", "lunch", "dinner", "snack"]
#: Longest source description kept on a stop or alternative.
SOURCE_SUMMARY_MAX = 400


class PlanAlternative(BaseModel):
    """Another real choice for a stop, with the same source facts as the stop itself.

    Every field comes from a place source except ``distance_m``, which the app computes
    (straight line from the default stop). A fact the source did not give stays empty.
    """

    id: str
    name: str
    geo: GeoPoint | None = None
    address: str = ""
    rating: float | None = None
    review_count: int | None = None
    price_level: str | None = None
    cuisine: str | None = None
    reservable: bool | None = None
    opening_hours: list[str] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    booking_url: str = ""
    distance_m: int | None = None
    #: The place source's own type labels ("indian_restaurant", "vegetarian_restaurant"); empty when it gave none.
    source_types: list[str] = Field(default_factory=list)
    #: The source's own short description, cut to SOURCE_SUMMARY_MAX characters; empty when it gave none.
    source_summary: str = ""


class PlanItem(BaseModel):
    id: str = Field(default_factory=lambda: new_id("item"))
    type: ItemType
    name: str
    start: TimeStr
    end: TimeStr
    geo: GeoPoint | None = None
    address: str = ""
    notes: str = ""
    source_refs: list[SourceRef] = Field(default_factory=list)
    data_status: DataStatus = DataStatus.OK
    fetched_at: datetime = Field(default_factory=utcnow)
    price: Money | None = None
    price_kind: PriceKind = PriceKind.NONE
    booking_url: str = ""
    locked: bool = False
    confirmed: bool = False
    tags: list[str] = Field(default_factory=list)
    # Opening hours exactly as the place source stated them for the visit day.
    # "unknown" means the source gave none; the planner never fills them in.
    opening_hours: list[str] = Field(default_factory=list)
    hours_status: Literal["stated", "unknown"] = "unknown"
    transfer: TransferLeg | None = None
    #: Localized names a place source supplied ("en", "zh-Hant"); display data only,
    #: never written by the model. Empty when no source named the place that way.
    names: dict[str, str] = Field(default_factory=dict)
    #: Which meal a meal stop is (None on other items and on plans stored before it existed).
    meal_slot: MealSlot | None = None
    #: Facts exactly as the place source gave them; None when it gave none.
    rating: float | None = None
    review_count: int | None = None
    price_level: str | None = None
    cuisine: str | None = None
    #: Only set when the source said whether the place takes reservations.
    reservable: bool | None = None
    #: The place source's own type labels and short description (see ``PlanAlternative``); empty when none.
    source_types: list[str] = Field(default_factory=list)
    source_summary: str = ""
    #: Other source-backed choices for this stop; the item itself is the default.
    alternatives: list[PlanAlternative] = Field(default_factory=list)

    @model_validator(mode="after")
    def _times(self) -> "PlanItem":
        if self.end <= self.start:
            raise ValueError(f"item {self.name!r} ends before it starts")
        if self.type == ItemType.ATTRACTION and not self.source_refs:
            raise ValueError(f"attraction {self.name!r} needs at least one source")
        return self


class DayPlan(BaseModel):
    date: date
    title: str = ""
    weather: WeatherDay | None = None
    items: list[PlanItem] = Field(default_factory=list)
    note: str = ""
    # Positive ascent in metres between the day's stops (Google Elevation).
    elevation_gain_m: float | None = None

    @model_validator(mode="after")
    def _no_overlap(self) -> "DayPlan":
        ordered = sorted(self.items, key=lambda item: item.start)
        for previous, current in zip(ordered, ordered[1:]):
            if current.start < previous.end:
                raise ValueError(f"items overlap on {self.date}: {previous.name} / {current.name}")
        return self


class RoomType(BaseModel):
    """One room the hotel source lists for the stay's dates, fetched only when the traveller opens the hotel."""

    name: str
    price_per_night: Money | None = None
    #: The seller the source listed the room under ("Booking.com"); empty when it named none.
    source: str = ""
    link: str = ""


class HotelNearby(BaseModel):
    """A way to reach a place near the hotel, exactly as the hotel source listed it ("Taxi", "11 min")."""

    name: str
    type: str = ""
    duration: str = ""


class HotelStay(BaseModel):
    id: str = Field(default_factory=lambda: new_id("hotel"))
    name: str
    area: str = ""
    geo: GeoPoint | None = None
    check_in: date
    check_out: date
    price_per_night: Money | None = None
    price_kind: PriceKind = PriceKind.NONE
    rating: float | None = None
    review_count: int | None = None
    distance_to_centre_km: float | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)
    data_status: DataStatus = DataStatus.OK
    fetched_at: datetime = Field(default_factory=utcnow)
    booking_url: str = ""
    notes: str = ""
    confirmed: bool = False
    locked: bool = False
    #: Facts as the hotel source gave them; empty when it gave none.
    amenities: list[str] = Field(default_factory=list)
    hotel_class: int | None = None
    #: The source's handle for the property, used to fetch its rooms on demand.
    property_token: str = ""
    nearby: list[HotelNearby] = Field(default_factory=list)
    #: Rooms from the source's property details; empty until fetched, or when the source lists none.
    room_types: list[RoomType] = Field(default_factory=list)
    #: The check-in and check-out the hotel source was last asked for rooms with; None until it was asked.
    #: ``room_types`` (possibly empty) belong to these dates.
    rooms_checked_for: DateRange | None = None
    #: Straight-line km to the closest planned stop, computed by the app; None without coordinates.
    nearest_stop_km: float | None = None
    nearest_stop_name: str = ""


class TicketOption(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ticket"))
    mode: Literal["flight", "train", "ticket", "ferry", "bus"]
    name: str
    origin: str = ""
    destination: str = ""
    depart_at: datetime | None = None
    arrive_at: datetime | None = None
    duration_minutes: int | None = None
    # Round-trip flights carry the inbound leg on the same option so the fare is
    # never split or double-counted.
    return_origin: str = ""
    return_destination: str = ""
    return_depart_at: datetime | None = None
    return_arrive_at: datetime | None = None
    return_duration_minutes: int | None = None
    return_airline: str = ""
    return_flight_number: str = ""
    price: Money | None = None
    price_kind: PriceKind = PriceKind.NONE
    operator: str = ""
    booking_url: str = ""
    notes: str = ""
    source_refs: list[SourceRef] = Field(default_factory=list)
    data_status: DataStatus = DataStatus.OK
    fetched_at: datetime = Field(default_factory=utcnow)
    confirmed: bool = False
    #: The option the traveller goes with; exactly one per plan (the first when none was chosen).
    selected: bool = False


def selected_ticket(tickets: list[TicketOption]) -> TicketOption | None:
    """The chosen option: the one marked selected, else the first (plans stored before the mark existed)."""
    return next((ticket for ticket in tickets if ticket.selected), tickets[0] if tickets else None)


class CostLine(BaseModel):
    label: str
    amount: float
    kind: str = "estimate"
    note: str = ""


class CostBreakdown(BaseModel):
    currency: str = "USD"
    lines: list[CostLine] = Field(default_factory=list)
    total: float = 0.0
    budget: float | None = None
    remaining: float | None = None
    status: Literal["within", "over", "unknown"] = "unknown"
    notes: list[str] = Field(default_factory=list)


class ConstraintCheck(BaseModel):
    id: str = Field(default_factory=lambda: new_id("check"))
    text: str
    status: ConstraintStatus = ConstraintStatus.UNKNOWN
    evidence: str = ""


class Disruption(BaseModel):
    id: str = Field(default_factory=lambda: new_id("disruption"))
    kind: str
    summary: str
    affected_item_ids: list[str] = Field(default_factory=list)
    resolution: str = ""
    resolved: bool = False


class Reservation(BaseModel):
    id: str = Field(default_factory=lambda: new_id("reservation"))
    name: str
    reason: str
    url: str = ""
    needed_by: date | None = None
    status: Literal["to_book", "optional", "booked"] = "to_book"


class DataGap(BaseModel):
    section: str
    kind: FailureKind
    message: str
    retryable: bool = False


class FreshnessMark(BaseModel):
    """A time-sensitive section checked against its freshness window (Proposal DB-13).

    ``outdated``: older than the window and not yet re-fetched. ``refreshed``: its
    own agent fetched it again at ``checked_at``; ``fetched_at`` is the old stamp.
    """

    section: Literal["weather", "tickets", "hotels"]
    status: Literal["outdated", "refreshed"]
    fetched_at: datetime
    checked_at: datetime
    window_seconds: int
    note: str = ""


class DataQuality(BaseModel):
    sources_used: list[str] = Field(default_factory=list)
    gaps: list[DataGap] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    search_window_months: int = 24
    generated_at: datetime = Field(default_factory=utcnow)
    freshness: list[FreshnessMark] = Field(default_factory=list)


class Destination(BaseModel):
    name: str
    country_code: str = ""
    geo: GeoPoint | None = None
    timezone: str = ""


class TripPlan(BaseModel):
    schema_version: str = "1.0"
    trip_id: str = Field(default_factory=lambda: new_id("trip"))
    session_id: str
    version: int = Field(default=1, ge=1)
    status: Literal["draft", "ready"] = "ready"
    destination: Destination
    origin: str = ""
    dates: DateRange
    party_size: int = Field(default=1, ge=1, le=20)
    budget: Money | None = None
    summary: str = ""
    days: list[DayPlan] = Field(default_factory=list)
    hotels: list[HotelStay] = Field(default_factory=list)
    #: Source-backed stays to choose from (up to 5); ``hotels[0]`` is the chosen one and is among them.
    hotel_options: list[HotelStay] = Field(default_factory=list)
    tickets: list[TicketOption] = Field(default_factory=list)
    cost: CostBreakdown = Field(default_factory=CostBreakdown)
    constraints: list[ConstraintCheck] = Field(default_factory=list)
    disruptions: list[Disruption] = Field(default_factory=list)
    reservations: list[Reservation] = Field(default_factory=list)
    data_quality: DataQuality = Field(default_factory=DataQuality)
    # Short public videos that ground the destination (YouTube / Bilibili).
    video_notes: list["NoteItem"] = Field(default_factory=list)
    change_summary: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _shape(self) -> "TripPlan":
        dates = [day.date for day in self.days]
        if dates and dates != self.dates.dates:
            raise ValueError("days must cover the trip date range exactly and in order")
        ids = [item.id for day in self.days for item in day.items]
        ids += [hotel.id for hotel in self.hotels]
        ids += [ticket.id for ticket in self.tickets]
        if len(ids) != len(set(ids)):
            raise ValueError("plan item ids must be unique")
        if self.cost.total < 0:
            raise ValueError("cost total cannot be negative")
        if self.tickets:
            # Exactly one option is the chosen one: the first marked, else the first.
            chosen = selected_ticket(self.tickets)
            for ticket in self.tickets:
                ticket.selected = ticket is chosen
        return self

    @property
    def item_count(self) -> int:
        return sum(len(day.items) for day in self.days)

    def iter_items(self):
        for day in self.days:
            for item in day.items:
                yield day, item

    def find_item(self, item_id: str) -> tuple[DayPlan, PlanItem] | None:
        for day, item in self.iter_items():
            if item.id == item_id:
                return day, item
        return None

    def find_hotel(self, hotel_id: str) -> HotelStay | None:
        for hotel in self.hotels:
            if hotel.id == hotel_id:
                return hotel
        return None

    def scope_name(self, scope_id: str) -> str | None:
        """Human label for a selected scope id (day item or stay)."""
        found = self.find_item(scope_id)
        if found:
            return found[1].name
        hotel = self.find_hotel(scope_id)
        return hotel.name if hotel else None

    def summary_for_prompt(self) -> dict[str, Any]:
        """Small, stable projection used in router/planner prompts."""
        return {
            "trip_id": self.trip_id,
            "destination": self.destination.name,
            "dates": [self.dates.start.isoformat(), self.dates.end.isoformat()],
            "party_size": self.party_size,
            "budget": self.budget.model_dump() if self.budget else None,
            "version": self.version,
            "days": [
                {
                    "date": day.date.isoformat(),
                    "title": day.title,
                    "items": [
                        {
                            "id": item.id,
                            "type": item.type.value,
                            "name": item.name,
                            "start": item.start,
                            "end": item.end,
                            "data_status": item.data_status.value,
                            "locked": item.locked,
                            "confirmed": item.confirmed,
                        }
                        for item in day.items
                    ],
                }
                for day in self.days
            ],
        }


# ---------------------------------------------------------------------------
# Conversation, context, memory
# ---------------------------------------------------------------------------

#: What a trip's form keeps, whoever writes it: TRIP_LINES_MAX preferences and as many constraints of
#: TRIP_LINE_MAX_LENGTH characters, and TRIP_FIELD_MAX_LENGTH characters in each of its other texts. A request over
#: them is refused (PATCH /api/journeys/{id}/state); what the system itself writes, and a form stored before the
#: limits, is cut to them (`TripContext.fit_to_limits`), so the page can always send back the form it was shown.
TRIP_LINES_MAX = 50
TRIP_LINE_MAX_LENGTH = 200
TRIP_FIELD_MAX_LENGTH = 120


#: TripContext's marks of the check before planning (application/budget_check.py).
_CHECK_MARKS = ("budget_confirmed", "past_dates_confirmed")


class TripContext(BaseModel):
    session_id: str
    destination: str = ""
    origin: str = ""
    start_date: date | None = None
    end_date: date | None = None
    days: int | None = Field(default=None, ge=1, le=60)
    party_size: int | None = Field(default=None, ge=1, le=20)
    budget: float | None = Field(default=None, ge=0)
    currency: str = "HKD"
    hotel_style: str = ""
    pace: str = "balanced"
    preferences: list[str] = Field(default_factory=list)
    hard_constraints: list[str] = Field(default_factory=list)
    language: str = "en"
    #: the plan's display language; the English plan stays the source of truth
    plan_locale: Literal["en", "zh-Hant"] = "en"
    #: The traveller chose to plan with this budget although it is below the per-night floor (or zero): the
    #: check before planning does not ask again. Another budget clears it.
    budget_confirmed: bool = False
    #: The traveller chose to keep a start date that has gone by: the check before planning does not ask again.
    past_dates_confirmed: bool = False
    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _within_limits(self) -> "TripContext":
        return self.fit_to_limits()

    @model_serializer(mode="wrap")
    def _marks_only_when_set(self, handler: Any) -> Any:
        """The check's marks are written only when set: a form without them dumps as it did before they existed, so
        the stored form, the API's and the prompts' trip JSON are unchanged for every trip that was not asked about."""
        data = handler(self)
        if isinstance(data, dict):
            for key in _CHECK_MARKS:
                if key in data and not data[key]:
                    del data[key]
        return data

    def fit_to_limits(self) -> "TripContext":
        """Cut the form to the limits above, in place: the newest lines of a long list, the first characters of a
        long text. The store does this before it writes (the understanding step appends to the lists in place, so
        nothing else sees what it left) and a form read back is cut the same way, so one stored before the limits
        cannot slow the service. The work is bounded by the limits, not by what the form held."""
        self.destination = self.destination[:TRIP_FIELD_MAX_LENGTH]
        self.origin = self.origin[:TRIP_FIELD_MAX_LENGTH]
        self.currency = self.currency[:TRIP_FIELD_MAX_LENGTH]
        self.hotel_style = self.hotel_style[:TRIP_FIELD_MAX_LENGTH]
        self.pace = self.pace[:TRIP_FIELD_MAX_LENGTH]
        self.language = self.language[:TRIP_FIELD_MAX_LENGTH]
        self.preferences = [line[:TRIP_LINE_MAX_LENGTH] for line in self.preferences[-TRIP_LINES_MAX:]]
        self.hard_constraints = [line[:TRIP_LINE_MAX_LENGTH] for line in self.hard_constraints[-TRIP_LINES_MAX:]]
        return self

    def missing_slots(self) -> list[str]:
        missing: list[str] = []
        if not self.destination.strip():
            missing.append("destination")
        if not self.origin.strip():
            missing.append("origin")
        if not self.start_date:
            missing.append("dates")
        elif not self.end_date and not self.days:
            missing.append("dates")
        if not self.party_size:
            missing.append("party_size")
        if self.budget is None:
            missing.append("budget")
        return missing

    def date_range(self) -> DateRange | None:
        if self.start_date and self.end_date:
            return DateRange(start=self.start_date, end=self.end_date)
        if self.start_date and self.days:
            return DateRange(
                start=self.start_date,
                end=date.fromordinal(self.start_date.toordinal() + self.days - 1),
            )
        return None


class RouteDecision(BaseModel):
    intent: Intent
    confidence: float = Field(ge=0, le=1)
    need_clarification: bool = False
    reason_code: str = ""
    affected_parts: list[str] = Field(default_factory=list)
    missing_slots: list[str] = Field(default_factory=list)
    related_to_plan: bool = True
    rationale: str = ""
    #: separate enum slots read by the rules: ``language`` (en | zh-Hant | other)
    #: and ``cuisine``; a language phrase never fills the cuisine slot
    slots: dict[str, str] = Field(default_factory=dict)


class Message(BaseModel):
    id: str = Field(default_factory=lambda: new_id("msg"))
    session_id: str
    role: Literal["user", "assistant", "system"]
    content: str
    intent: Intent | None = None
    confidence: float | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)


class User(BaseModel):
    id: str = Field(default_factory=lambda: new_id("user"))
    email: str
    display_name: str = ""
    created_at: datetime = Field(default_factory=utcnow)


#: Profile token prefix of a signed-in account: `user:<id>`.
USER_PREFIX = "user:"


class Session(BaseModel):
    id: str = Field(default_factory=lambda: new_id("sess"))
    client_token: str = ""
    user_id: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def owner(self) -> str:
        """Identity that owns this session's memory: `user:<id>`, else its guest token.

        A `user:` client token on a session with no account was never proven
        with a bearer, so it owns nothing (empty string).
        """
        if self.user_id:
            return f"{USER_PREFIX}{self.user_id}"
        token = (self.client_token or "").strip()
        return "" if token.startswith(USER_PREFIX) else token


class Feedback(BaseModel):
    id: str = Field(default_factory=lambda: new_id("fb"))
    trip_id: str
    useful: bool
    stars: int | None = Field(default=None, ge=1, le=5)
    reason_code: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class SavedTrip(BaseModel):
    id: str = Field(default_factory=lambda: new_id("saved"))
    trip_id: str
    destination: str
    rating: float = 0.0
    stars: int | None = None
    summary: str = ""
    embedding: list[float] = Field(default_factory=list)
    plan: TripPlan | None = None
    created_at: datetime = Field(default_factory=utcnow)


#: What a traveller profile keeps, whoever writes it: PREFERENCES_MAX phrases of PREFERENCE_MAX_LENGTH characters.
PREFERENCES_MAX = 20
PREFERENCE_MAX_LENGTH = 80


def fit_preference(text: str) -> str:
    """One preference as a profile keeps it: trimmed, and cut to PREFERENCE_MAX_LENGTH characters."""
    return text.strip()[:PREFERENCE_MAX_LENGTH].rstrip()


class PreferenceProfile(BaseModel):
    client_token: str
    #: Oldest first. Every profile that is built or read is fitted to the limits above, so what the
    #: turns, feedback and the sign-in merge write can always be sent back by the Settings page.
    preferences: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utcnow)

    @model_validator(mode="after")
    def _fit(self) -> "PreferenceProfile":
        # A dict keeps the first of each repeat in order, in one pass: a profile stored before the limits
        # may hold any number of phrases, and `value not in kept` took seconds on thousands of them.
        kept = list(dict.fromkeys(value for value in map(fit_preference, self.preferences) if value))
        self.preferences = kept[-PREFERENCES_MAX:]
        return self


# ---------------------------------------------------------------------------
# Agent and provider DTOs
# ---------------------------------------------------------------------------


class AgentResult(BaseModel):
    agent: AgentName
    status: AgentStatus
    payload: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)
    failures: list[DataGap] = Field(default_factory=list)
    elapsed_ms: int = 0
    fetched_at: datetime = Field(default_factory=utcnow)
    #: True when the agent was cut off and this is what it had found so far. Its status is PARTIAL
    #: as well; the console shows the status and the gap.
    partial: bool = False


class AuditSeverity(StrEnum):
    INFO = "info"
    WARN = "warn"
    BLOCK = "block"


class AuditIssue(StrEnum):
    SUSPECT = "suspect_fact"
    STALE = "stale"
    OFF_REQUEST = "off_request"
    INCONSISTENT = "inconsistent"
    MISSING = "missing"


class AuditFollowUp(BaseModel):
    """A web search the audit asks for to check one finding."""

    query: str = Field(min_length=3, max_length=160)


class AuditFinding(BaseModel):
    agent: AgentName
    item_id: str | None = None
    issue: AuditIssue
    severity: AuditSeverity
    detail: str = Field(max_length=300)
    follow_up: AuditFollowUp | None = None


class FollowUpEvidence(BaseModel):
    """What a follow-up search found: reference material only, never a price or a temperature."""

    finding_index: int
    query: str
    references: list[SourceRef] = Field(default_factory=list)
    gap: DataGap | None = None


class AuditReport(BaseModel):
    findings: list[AuditFinding] = Field(default_factory=list)
    evidence: list[FollowUpEvidence] = Field(default_factory=list)
    #: Why the audit did not run or finished early; the trip plans either way.
    skipped: str | None = None

    @property
    def blocked_ids(self) -> set[str]:
        return {
            f.item_id for f in self.findings
            if f.severity == AuditSeverity.BLOCK and f.item_id is not None
        }


class PlaceCandidate(BaseModel):
    name: str
    category: str = "attraction"
    description: str = ""
    geo: GeoPoint | None = None
    address: str = ""
    rating: float | None = None
    review_count: int | None = None
    opening_hours: list[str] = Field(default_factory=list)
    price_level: str = ""
    website: str = ""
    #: a few real reviews: what visitors praise or complain about. Without these
    #: an opinion question ("is it fun?") can only restate the editorial summary.
    reviews: list[dict[str, Any]] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    #: other names the source knows the place by ("Lotte World" for 롯데월드);
    #: also used to merge duplicates across scripts (伏見稲荷大社 / Fushimi Inari Taisha).
    aliases: list[str] = Field(default_factory=list)
    #: the source's own localized names, keyed "en" / "zh-Hant" (OSM name:en, name:zh-HK, ...).
    names: dict[str, str] = Field(default_factory=dict)
    #: whether the place takes reservations, only when the source said so.
    reservable: bool | None = None
    #: cuisine label read from the source's place type ("ramen_restaurant" -> "Ramen"); None otherwise.
    cuisine: str | None = None
    #: the source's own price range text ("JPY 1,000-2,000"); empty when it gave none.
    price_range: str = ""


class HotelCandidate(BaseModel):
    name: str
    area: str = ""
    geo: GeoPoint | None = None
    nightly_price: Money | None = None
    price_kind: PriceKind = PriceKind.NONE
    rating: float | None = None
    review_count: int | None = None
    distance_to_centre_km: float | None = None
    booking_url: str = ""
    source_refs: list[SourceRef] = Field(default_factory=list)
    notes: str = ""
    #: Facts the hotel source returned; empty when it returned none.
    amenities: list[str] = Field(default_factory=list)
    hotel_class: int | None = None
    property_token: str = ""
    nearby: list[HotelNearby] = Field(default_factory=list)


class FlightCandidate(BaseModel):
    airline: str = ""
    flight_number: str = ""
    origin: str
    destination: str
    depart_at: datetime
    arrive_at: datetime
    duration_minutes: int | None = None
    return_origin: str = ""
    return_destination: str = ""
    return_depart_at: datetime | None = None
    return_arrive_at: datetime | None = None
    return_duration_minutes: int | None = None
    return_airline: str = ""
    return_flight_number: str = ""
    price: Money | None = None
    price_kind: PriceKind = PriceKind.REFERENCE
    booking_url: str = ""
    source_refs: list[SourceRef] = Field(default_factory=list)


class NoteItem(BaseModel):
    title: str
    url: str
    source: str
    snippet: str = ""
    video_id: str = ""
    thumbnail_url: str = ""
    published_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=utcnow)


class RouteStep(BaseModel):
    """One step inside a routed leg. WALK routes can include a ferry step."""

    mode: str = "walk"
    minutes: int | None = None
    polyline: list[GeoPoint] = Field(default_factory=list)
    instruction: str = ""


class RouteLeg(BaseModel):
    from_geo: GeoPoint
    to_geo: GeoPoint
    duration_minutes: int | None = None
    distance_km: float | None = None
    mode: str = "walk"
    source: str = "estimate"
    polyline: list[GeoPoint] = Field(default_factory=list)
    steps: list[RouteStep] = Field(default_factory=list)
    # Set by a source that returns many legs in one result (routes.segments), so
    # a plan can name the exact leg it used. Read as attributes only: they are
    # left out of JSON dumps, so prompts and API payloads keep their shape.
    id: str = Field(default="", exclude=True)
    from_id: str = Field(default="", exclude=True)
    to_id: str = Field(default="", exclude=True)


class WeatherAlert(BaseModel):
    """An official weather warning in force on a date (Hong Kong Observatory)."""

    date: date
    code: str
    name: str
    # Severe warnings (typhoon signal No. 3 and above, red or black rainstorm,
    # tsunami) move outdoor stops off the day; the others are a note.
    severe: bool = False
    source: str = ""
    url: str = ""
    issued_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=utcnow)


class TransitStatus(BaseModel):
    """The service status one rail line reported for a date (MTR Next Train)."""

    line: str
    line_name: str = ""
    date: date
    delayed: bool = False
    # Only when the source states it; the MTR feed reports a delay flag only.
    delay_minutes: int | None = None
    message: str = ""
    source: str = ""
    url: str = ""
    fetched_at: datetime = Field(default_factory=utcnow)


class GeoLocation(BaseModel):
    name: str
    country_code: str = ""
    timezone: str = ""
    geo: GeoPoint
    source: str = ""


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


class EvalCase(BaseModel):
    id: str
    dataset: str
    split: Literal["dev", "heldout"] = "dev"
    kind: Literal["routing", "planning", "injection", "disruption", "edit"] = "planning"
    input: str
    expected_intent: Intent | None = None
    destination: str = ""
    origin: str = ""
    days: int | None = None
    party_size: int | None = None
    budget: float | None = None
    currency: str = "USD"
    constraints: list[str] = Field(default_factory=list)
    expected_facts: list[str] = Field(default_factory=list)
    fault: str = ""
    # What the sandbox injects for `fault` (day, venue, delay minutes, report text).
    fault_detail: dict[str, Any] = Field(default_factory=dict)


class DatasetMeta(BaseModel):
    id: str
    name: str
    version: str = "1.0"
    split: Literal["dev", "heldout"] = "dev"
    frozen: bool = False
    item_count: int = 0
    created_at: datetime = Field(default_factory=utcnow)


class EvalMetric(BaseModel):
    key: str
    label: str
    value: float
    unit: str = "%"
    baseline: float | None = None
    delta: float | None = None
    direction: Literal["up", "down", "flat"] = "flat"


class EvalCaseResult(BaseModel):
    id: str = Field(default_factory=lambda: new_id("case"))
    case_id: str
    dataset: str
    split: str
    passed: bool
    score: float = 0.0
    failure_class: str = ""
    input: str = ""
    output_summary: str = ""
    judge_reason: str = ""
    judge_score: float | None = None
    judge_flags: list[str] = Field(default_factory=list)
    tool_summary: list[str] = Field(default_factory=list)
    scores: dict[str, float] = Field(default_factory=dict)
    # Planning cases: per-constraint verdicts, disruption outcomes, recovery and
    # grounding checks (see app.eval_checks).
    plan_checks: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = ""


class EvaluationRun(BaseModel):
    id: str = Field(default_factory=lambda: new_id("run"))
    experiment_tag: str
    dataset_id: str
    dataset_version: str = "1.0"
    status: Literal["running", "completed", "failed"] = "completed"
    metrics: list[EvalMetric] = Field(default_factory=list)
    cases: list[EvalCaseResult] = Field(default_factory=list)
    model_snapshot: dict[str, Any] = Field(default_factory=dict)
    token_usage: int = 0
    latency_ms: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


class TurnEvent(BaseModel):
    """One SSE event. `data` is JSON-serialisable."""

    event: str
    data: dict[str, Any] = Field(default_factory=dict)


# TripPlan references NoteItem, which is declared later in this module.
TripPlan.model_rebuild()
