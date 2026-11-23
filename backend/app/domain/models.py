"""The wire model of a Pathfinder trip.

These types are the contract the React UI was written against (``frontend/src/design-system/types.ts``).
Everything the API returns is one of these models, so a field the UI reads can
never be missing because some code path forgot it: defaults are part of the model.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.currencies import normalise_currency

DataStatus = Literal["ok", "stale", "unavailable"]
ItemType = Literal["attraction", "hotel", "meal", "transit", "ticket"]
Intent = Literal["plan", "modify", "ask", "unclear"]
PriceKind = Literal["live", "reference", "estimate", "none"]
TransportMode = Literal["flight", "train", "ticket", "ferry", "bus", "shuttle", "taxi", "drive", "ground"]
MealSlot = Literal["breakfast", "lunch", "dinner", "snack"]
PlanLocale = Literal["en", "zh-Hant", "zh-Hans"]
LinkKind = Literal["booking", "official", "map", "tickets", "menu"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Wire(BaseModel):
    """Base: lenient on input (sources evolve), strict on the fields the UI relies on."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class GeoPoint(Wire):
    lat: float
    lng: float


class Money(Wire):
    amount: float
    currency: str = "HKD"


class SourceRef(Wire):
    title: str
    url: str = ""
    source: str = ""
    fetched_at: str | None = None


class ImageRef(Wire):
    url: str
    attribution: str = ""
    source: Literal["google", "wikimedia", "serpapi"] = "wikimedia"


class Link(Wire):
    kind: LinkKind
    url: str
    label: str = ""


class WeatherDay(Wire):
    date: str
    status: DataStatus = "ok"
    summary: str = ""
    high_c: float | None = None
    low_c: float | None = None
    precipitation_probability: float | None = None
    source: str = ""
    fetched_at: str | None = None
    note: str = ""


class TransferLeg(Wire):
    from_item_id: str
    minutes: float
    mode: str = "walk"
    distance_km: float | None = None
    source: str = ""
    tool: str = ""
    result_id: str = ""


class PlanAlternative(Wire):
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
    distance_m: float | None = None
    source_types: list[str] = Field(default_factory=list)
    source_summary: str = ""
    links: list[Link] = Field(default_factory=list)
    image: ImageRef | None = None
    rank: int | None = None
    score: float | None = None
    reasons: list[str] = Field(default_factory=list)


class PlanItem(Wire):
    id: str
    type: ItemType = "attraction"
    name: str
    start: str = ""
    end: str = ""
    geo: GeoPoint | None = None
    address: str = ""
    notes: str = ""
    source_refs: list[SourceRef] = Field(default_factory=list)
    data_status: DataStatus = "ok"
    fetched_at: str | None = None
    price: Money | None = None
    price_kind: PriceKind = "none"
    booking_url: str = ""
    locked: bool = False
    confirmed: bool = False
    tags: list[str] = Field(default_factory=list)
    opening_hours: list[str] = Field(default_factory=list)
    hours_status: Literal["stated", "unknown"] = "unknown"
    transfer: TransferLeg | None = None
    names: dict[str, str] = Field(default_factory=dict)
    meal_slot: MealSlot | None = None
    rating: float | None = None
    review_count: int | None = None
    price_level: str | None = None
    cuisine: str | None = None
    reservable: bool | None = None
    source_types: list[str] = Field(default_factory=list)
    source_summary: str = ""
    alternatives: list[PlanAlternative] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    image: ImageRef | None = None
    step_free: bool | None = None


class DayPlan(Wire):
    date: str
    title: str = ""
    weather: WeatherDay | None = None
    items: list[PlanItem] = Field(default_factory=list)
    note: str = ""
    elevation_gain_m: float | None = None
    segment_id: str | None = None
    # The city for this day in a multi-city plan; empty for a single-city trip.
    city: str = ""


class RoomType(Wire):
    name: str
    price_per_night: Money | None = None
    source: str = ""
    link: str = ""


class HotelNearby(Wire):
    name: str
    type: str = ""
    duration: str = ""


class StayMatch(Wire):
    wishes: list[str] = Field(default_factory=list)
    verdict: Literal["meets", "partly", "unclear", "fails"] = "unclear"
    score: float = 0
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)
    evidence_refs: list[SourceRef] = Field(default_factory=list)
    rooms_checked: bool = False


class HotelStay(Wire):
    id: str
    name: str
    area: str = ""
    geo: GeoPoint | None = None
    check_in: str = ""
    check_out: str = ""
    price_per_night: Money | None = None
    price_kind: PriceKind = "none"
    rating: float | None = None
    review_count: int | None = None
    distance_to_centre_km: float | None = None
    source_refs: list[SourceRef] = Field(default_factory=list)
    data_status: DataStatus = "ok"
    booking_url: str = ""
    notes: str = ""
    confirmed: bool = False
    locked: bool = False
    booked: bool = False
    names: dict[str, str] = Field(default_factory=dict)
    amenities: list[str] = Field(default_factory=list)
    hotel_class: int | None = None
    property_token: str = ""
    nearby: list[HotelNearby] = Field(default_factory=list)
    room_types: list[RoomType] = Field(default_factory=list)
    rooms_checked_for: dict[str, str] | None = None
    nearest_stop_km: float | None = None
    nearest_stop_name: str = ""
    match: StayMatch | None = None
    segment_id: str | None = None
    links: list[Link] = Field(default_factory=list)
    image: ImageRef | None = None
    rank: int | None = None
    score: float | None = None
    reasons: list[str] = Field(default_factory=list)


class TicketOption(Wire):
    id: str
    mode: TransportMode = "ticket"
    leg_id: str | None = None
    estimated: bool = False
    links: list[Link] = Field(default_factory=list)
    rank: int | None = None
    score: float | None = None
    reasons: list[str] = Field(default_factory=list)
    name: str
    origin: str = ""
    destination: str = ""
    depart_at: str | None = None
    arrive_at: str | None = None
    duration_minutes: float | None = None
    return_origin: str = ""
    return_destination: str = ""
    return_depart_at: str | None = None
    return_arrive_at: str | None = None
    return_duration_minutes: float | None = None
    return_airline: str = ""
    return_flight_number: str = ""
    price: Money | None = None
    price_kind: PriceKind = "none"
    operator: str = ""
    booking_url: str = ""
    notes: str = ""
    source_refs: list[SourceRef] = Field(default_factory=list)
    data_status: DataStatus = "ok"
    confirmed: bool = False
    selected: bool = False


class TripSegment(Wire):
    id: str
    destination: dict[str, Any] = Field(default_factory=dict)
    dates: dict[str, str] = Field(default_factory=dict)


class TransportLeg(Wire):
    id: str
    from_place: str
    to_place: str
    depart_date: str | None = None
    kind: Literal["outbound", "between", "return", "to_stay", "from_stay"] = "outbound"
    locked: bool = False
    booked: bool = False
    segment_id: str | None = None
    airport: str = ""


class TripStop(Wire):
    city: str
    nights: int | None = None


class ImportedStop(Wire):
    """A named stop read from a traveller's itinerary image or document."""

    name: str
    city: str = ""
    kind: Literal["attraction", "meal", "hotel", "ticket"] = "attraction"
    start: str = ""
    end: str = ""
    note: str = ""


class ImportedDay(Wire):
    day: int | None = None
    date: str | None = None
    city: str = ""
    title: str = ""
    stops: list[ImportedStop] = Field(default_factory=list)

    @field_validator("stops", mode="before")
    @classmethod
    def _stops(cls, value: Any) -> Any:
        # A vision model occasionally returns bare names instead of objects.
        if isinstance(value, list):
            return [{"name": item} if isinstance(item, str) else item for item in value if item]
        return value


class ImportedItinerary(Wire):
    """The day-by-day structure the intake model reads from an uploaded itinerary.

    It is deliberately separate from ``TripPlan``: an import is a source, not a
    verified plan. The importer looks each named stop up and leaves a gap when it
    cannot be sourced.
    """

    summary: str = ""
    days: list[ImportedDay] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    hotels: list[ImportedStop] = Field(default_factory=list)

    @field_validator("hotels", mode="before")
    @classmethod
    def _hotels(cls, value: Any) -> Any:
        if isinstance(value, list):
            return [{"name": item, "kind": "hotel"} if isinstance(item, str) else item for item in value if item]
        return value


class CostLine(Wire):
    label: str
    amount: float = 0
    kind: str = ""
    note: str = ""


class CostBreakdown(Wire):
    currency: str = "HKD"
    lines: list[CostLine] = Field(default_factory=list)
    total: float = 0
    budget: float | None = None
    remaining: float | None = None
    status: Literal["within", "over", "unknown"] = "unknown"
    notes: list[str] = Field(default_factory=list)


class ConstraintCheck(Wire):
    id: str
    text: str
    status: Literal["pass", "fail", "unknown"] = "unknown"
    evidence: str = ""


class FreshnessMark(Wire):
    section: Literal["weather", "tickets", "hotels"]
    status: Literal["outdated", "refreshed"]
    fetched_at: str
    checked_at: str
    window_seconds: float
    note: str = ""


class DataGap(Wire):
    section: str
    kind: str
    message: str
    retryable: bool = False


class Reservation(Wire):
    id: str
    name: str
    reason: str = ""
    url: str = ""
    needed_by: str | None = None
    status: Literal["to_book", "optional", "booked"] = "optional"


class VideoNote(Wire):
    title: str
    url: str
    source: str = ""
    snippet: str = ""
    video_id: str = ""
    thumbnail_url: str = ""
    published_at: str | None = None


class AssessmentSignal(Wire):
    code: str
    weight: float = 0
    message: str = ""


class PlanAssessment(Wire):
    confidence: float = 0
    level: Literal["high", "medium", "low"] = "low"
    signals: list[AssessmentSignal] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    unverified: list[str] = Field(default_factory=list)
    next_action: Literal["none", "research", "ask"] = "none"
    question: str = ""
    research_rounds: int = 0
    improvements: list[str] = Field(default_factory=list)


class Destination(Wire):
    name: str = ""
    country_code: str = ""
    geo: GeoPoint | None = None
    timezone: str = "UTC"


class DateRange(Wire):
    start: str = ""
    end: str = ""


class DataQuality(Wire):
    sources_used: list[str] = Field(default_factory=list)
    gaps: list[DataGap] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    search_window_months: int = 24
    generated_at: str = Field(default_factory=now_iso)
    freshness: list[FreshnessMark] = Field(default_factory=list)


class Disruption(Wire):
    id: str
    kind: str = ""
    summary: str = ""
    affected_item_ids: list[str] = Field(default_factory=list)
    resolution: str = ""
    resolved: bool = False


class TripPlan(Wire):
    schema_version: str = "5.0"
    trip_id: str
    session_id: str
    version: int = 1
    status: Literal["draft", "ready"] = "draft"
    destination: Destination = Field(default_factory=Destination)
    origin: str = ""
    dates: DateRange = Field(default_factory=DateRange)
    party_size: int = 1
    budget: Money | None = None
    summary: str = ""
    days: list[DayPlan] = Field(default_factory=list)
    hotels: list[HotelStay] = Field(default_factory=list)
    hotel_options: list[HotelStay] = Field(default_factory=list)
    tickets: list[TicketOption] = Field(default_factory=list)
    segments: list[TripSegment] = Field(default_factory=list)
    legs: list[TransportLeg] = Field(default_factory=list)
    cost: CostBreakdown = Field(default_factory=CostBreakdown)
    constraints: list[ConstraintCheck] = Field(default_factory=list)
    disruptions: list[Disruption] = Field(default_factory=list)
    reservations: list[Reservation] = Field(default_factory=list)
    video_notes: list[VideoNote] = Field(default_factory=list)
    data_quality: DataQuality = Field(default_factory=DataQuality)
    assessment: PlanAssessment | None = None
    change_summary: str = ""
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    def item_index(self) -> dict[str, PlanItem]:
        return {item.id: item for day in self.days for item in day.items}

    def item_count(self) -> int:
        return sum(len(day.items) for day in self.days)


class TravellerProfile(Wire):
    pace: Literal["relaxed", "balanced", "packed"] = "balanced"
    with_children: bool = False
    purpose: Literal["leisure", "business"] = "leisure"
    step_free: bool = False


class TripContext(Wire):
    @model_validator(mode="before")
    @classmethod
    def _fill_nulls(cls, data: Any) -> Any:
        """A context written by an older build can hold null strings; never 500 on load."""
        if not isinstance(data, dict):
            return data
        for key in ("session_id", "destination", "origin", "hotel_style"):
            if key in data and data[key] is None:
                data[key] = ""
        if data.get("currency") is None:
            data["currency"] = "HKD"
        if data.get("pace") is None:
            data["pace"] = "balanced"
        if data.get("language") is None:
            data["language"] = "en"
        if data.get("plan_locale") is None:
            data["plan_locale"] = "en"
        for key in ("preferences", "hard_constraints", "stops"):
            if key in data and data[key] is None:
                data[key] = []
        return data

    session_id: str
    destination: str = ""
    origin: str = ""
    start_date: str | None = None
    end_date: str | None = None
    days: int | None = None
    party_size: int | None = None
    budget: float | None = None
    currency: str = "HKD"
    hotel_style: str = ""
    pace: str = "balanced"
    profile: TravellerProfile | None = None
    preferences: list[str] = Field(default_factory=list)
    finalized: bool = False
    hard_constraints: list[str] = Field(default_factory=list)
    language: str = "en"
    plan_locale: PlanLocale = "en"
    stops: list[TripStop] = Field(default_factory=list)
    one_way: bool = False
    imported_itinerary: ImportedItinerary | None = None

    @field_validator("currency", mode="before")
    @classmethod
    def _currency(cls, value: Any) -> str:
        return normalise_currency(value) or "HKD"


class ChatMessage(Wire):
    id: str
    role: Literal["user", "assistant", "system"] = "assistant"
    content: str = ""
    intent: Intent | None = None
    confidence: float | None = None
    created_at: str = Field(default_factory=now_iso)
    meta: dict[str, Any] = Field(default_factory=dict)


class VersionEntry(Wire):
    trip_id: str
    version: int
    change_summary: str = ""
    created_at: str = Field(default_factory=now_iso)
    item_count: int = 0
    destination: str = ""
    days: int = 0


class AgentState(Wire):
    agent: str
    status: Literal["running", "ok", "partial", "unavailable", "timeout", "failed"] = "ok"
    elapsed_ms: float = 0
    summary: str = ""


class ClarifySuggestion(Wire):
    slot: str
    asked: str = ""
    name: str


class ClarifyPayload(Wire):
    question: str
    slots: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    options: dict[str, list[str]] = Field(default_factory=dict)
    choices: list[dict[str, str]] = Field(default_factory=list)
    suggestions: list[ClarifySuggestion] = Field(default_factory=list)
    reason_code: str = "missing_slots"


class RoutePayload(Wire):
    intent: Intent = "unclear"
    confidence: float = 0
    threshold: float = 0.8
    need_clarification: bool = False
    reason_code: str = ""
    affected_parts: list[str] = Field(default_factory=list)
    missing_slots: list[str] = Field(default_factory=list)
    related_to_plan: bool = False
    blocked: bool = False
    # A period the traveller named without a day ("next month"); the clarify
    # card uses it to ask for the date without inventing one.
    date_window: str = ""


class IntakeSource(Wire):
    name: str
    kind: Literal["url", "pdf", "image", "text"] = "text"
    status: Literal["read", "failed"] = "read"
    note: str = ""


class IntakePayload(Wire):
    sources: list[IntakeSource] = Field(default_factory=list)
    filled: list[str] = Field(default_factory=list)
    places: list[str] = Field(default_factory=list)


class TraceSpan(Wire):
    name: str
    duration_ms: float | None = None
    status: str | None = None


class GenerationTrace(Wire):
    purpose: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0
    reasoning: str = ""
    output_preview: str = ""
    user_preview: str = ""


class SearchTrace(Wire):
    provider: str = ""
    query: str = ""
    results: list[dict[str, Any]] = Field(default_factory=list)


class ToolTrace(Wire):
    name: str = ""
    status: str = "ok"
    latency_ms: float = 0
    args: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


class RunTrace(Wire):
    request_id: str
    spans: list[TraceSpan] = Field(default_factory=list)
    generations: list[GenerationTrace] = Field(default_factory=list)
    why: list[str] = Field(default_factory=list)
    flow: list[str] = Field(default_factory=list)
    path: dict[str, Any] | None = None
    reason: str = ""
    message: str = ""
    searches: list[SearchTrace] = Field(default_factory=list)
    tools: list[ToolTrace] = Field(default_factory=list)
    tool_calls: int = 0


class PlanTranslation(Wire):
    locale: PlanLocale = "en"
    plan_version: int = 1
    status: Literal["translated", "fallback", "unavailable", "english"] = "english"
    reason: str = ""
    disclaimer: str = ""
    fields: dict[str, Any] = Field(default_factory=dict)


class SessionState(Wire):
    session: dict[str, Any]
    context: TripContext
    plan: TripPlan | None = None
    versions: list[VersionEntry] = Field(default_factory=list)
    messages: list[ChatMessage] = Field(default_factory=list)
    held: bool = False
    agent_name: str = ""
    planner_busy: bool = False
    planner_note: str = ""


# --- request bodies -------------------------------------------------------


class BufferedAttachment(Wire):
    name: str
    media_type: str = "application/octet-stream"
    data: str = ""


class ChatRequest(Wire):
    session_id: str | None = None
    message: str = ""
    locale: str = "en"
    scope_item_id: str | None = None
    scope_label: str | None = None
    developer: bool = False
    review: bool = True
    client_token: str | None = None
    identity: str = ""
    attachments: list[BufferedAttachment] = Field(default_factory=list)


class CreateSessionRequest(Wire):
    client_token: str | None = None


class LayoutItem(Wire):
    id: str
    day: int
    start: str
    end: str
    name: str | None = None


class LayoutRequest(Wire):
    items: list[LayoutItem] = Field(default_factory=list)
    drops: list[str] = Field(default_factory=list)
    plan_version: int | None = None


class ChooseRequest(Wire):
    kind: Literal["item", "hotel", "flight", "transport"]
    target_id: str | None = None
    option_id: str
    plan_version: int | None = None
    lock: bool = False


class LockRequest(Wire):
    kind: Literal["hotel", "transport"]
    target_id: str
    locked: bool = True
    plan_version: int | None = None


class BookedRequest(Wire):
    kind: Literal["hotel", "transport"]
    target_id: str
    booked: bool = True
    plan_version: int | None = None


class RestoreRequest(Wire):
    version: int


class ProposalDecision(Wire):
    change_id: str
    accept: bool


class ImportRequest(Wire):
    client_token: str | None = None
    plan: dict[str, Any]


class FeedbackRequest(Wire):
    useful: bool
    stars: int | None = None
    client_token: str = ""


class PatchState(Wire):
    """Every field optional; a null leaves the value unchanged."""

    destination: str | None = None
    origin: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    days: int | None = None
    party_size: int | None = None
    budget: float | None = None
    currency: str | None = None
    hotel_style: str | None = None
    pace: str | None = None
    profile: TravellerProfile | None = None
    preferences: list[str] | None = None
    hard_constraints: list[str] | None = None
    language: str | None = None
    plan_locale: PlanLocale | None = None
    stops: list[TripStop] | None = None
    one_way: bool | None = None
    finalized: bool | None = None
