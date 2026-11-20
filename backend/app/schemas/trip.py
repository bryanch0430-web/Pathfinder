from datetime import date, datetime
from enum import Enum
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import (
    ConversationSessionStatus,
    ConversationStage,
    MessageRole,
    MessageType,
    OnboardingStatus,
    TripStatus,
)


# Session 2 API Schemas

class ConversationMessageResponse(BaseModel):
    id: UUID
    role: MessageRole
    message_type: MessageType
    content: Optional[str] = None
    payload: Optional[dict] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationSessionResponse(BaseModel):
    id: UUID
    trip_id: UUID
    status: ConversationSessionStatus
    current_stage: ConversationStage
    context_summary: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TripResponse(BaseModel):
    id: UUID
    user_id: Optional[str] = None
    guest_id: Optional[str] = None
    destination: Optional[str] = None
    places_to_visit: List[str] = Field(default_factory=list)
    planning_preferences: dict = Field(default_factory=dict)

    origin_text: Optional[str] = None
    origin_latitude: Optional[float] = None
    origin_longitude: Optional[float] = None
    duration_days: Optional[int] = None
    currency: str = "INR"
    status: TripStatus
    onboarding_status: OnboardingStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)



class TripStateResponse(BaseModel):
    trip: TripResponse
    conversation: ConversationSessionResponse
    assistant_message: Optional[ConversationMessageResponse] = None


class TripCreateResponse(BaseModel):
    trip_id: UUID
    session_id: UUID
    assistant_message: ConversationMessageResponse


class SendMessageRequest(BaseModel):
    message_type: MessageType = MessageType.TEXT
    content: Optional[str] = None
    payload: Optional[dict] = None

    @model_validator(mode="after")
    def validate_message_content_and_payload(self) -> "SendMessageRequest":
        if self.message_type == MessageType.TEXT:
            if not self.content or not self.content.strip():
                raise ValueError("Text message content must be non-empty.")
        elif self.message_type == MessageType.UI_ACTION:
            if not self.payload:
                raise ValueError("UI_ACTION message must include a non-null payload.")
            action = self.payload.get("action")
            if action == "SUBMIT_TRIP_ONBOARDING":
                self.payload = OnboardingFormSubmission.model_validate(self.payload).model_dump(mode="json")
                self.payload["action"] = action
                return self
            if action == "GENERATE_FULL_ITINERARY":
                return self
            if action == "START_BUILD_WITH_AGENT":
                self.payload = BuildWithAgentStart.model_validate(self.payload).model_dump(exclude_none=True)
                return self
            if action == "COPILOT_OPS":
                self.payload = CopilotOps.model_validate(self.payload).model_dump()
                return self
            if action == "SET_LOCATION":
                lat = self.payload.get("latitude") if self.payload.get("latitude") is not None else self.payload.get("origin_latitude")
                lon = self.payload.get("longitude") if self.payload.get("longitude") is not None else self.payload.get("origin_longitude")
                if lat is None or not isinstance(lat, (int, float)) or not (-90 <= lat <= 90):
                    raise ValueError("Latitude must be a float between -90 and 90.")
                if lon is None or not isinstance(lon, (int, float)) or not (-180 <= lon <= 180):
                    raise ValueError("Longitude must be a float between -180 and 180.")
            elif action == "SET_ORIGIN":
                origin_val = self.payload.get("origin_text") or self.payload.get("origin") or self.payload.get("label")
                if not origin_val or not str(origin_val).strip():
                    raise ValueError("SET_ORIGIN action requires a non-empty origin_text.")
            elif "origin_latitude" in self.payload and "origin_longitude" in self.payload:
                lat = self.payload.get("origin_latitude")
                lon = self.payload.get("origin_longitude")
                if lat is None or not isinstance(lat, (int, float)) or not (-90 <= lat <= 90):
                    raise ValueError("Latitude must be a float between -90 and 90.")
                if lon is None or not isinstance(lon, (int, float)) or not (-180 <= lon <= 180):
                    raise ValueError("Longitude must be a float between -180 and 180.")
            elif "origin_text" in self.payload:
                origin_val = self.payload.get("origin_text")
                if not origin_val or not str(origin_val).strip():
                    raise ValueError("origin_text must be non-empty.")
            else:
                raise ValueError(f"Unsupported UI_ACTION action: '{action}'")
        return self


class BuildWithAgentStart(BaseModel):
    action: Literal["START_BUILD_WITH_AGENT"]
    budget_amount: Optional[float] = Field(default=None, gt=0, le=1_000_000_000)
    currency: Optional[Literal["INR", "JPY", "USD", "EUR", "GBP", "AUD", "CAD"]] = None
    travel_mode: Optional[Literal["walk", "transit", "taxi", "drive"]] = None
    vibe: List[str] = Field(default_factory=list, max_length=5)


class CopilotOps(BaseModel):
    """Structured edits from the client (chips/buttons); the engine validates each op's fields."""
    action: Literal["COPILOT_OPS"]
    ops: List[dict] = Field(min_length=1, max_length=10)
    copilot_day: Optional[int] = Field(default=None, ge=1, le=365)


class PlanningPreferences(BaseModel):
    """The trip brief beyond route basics. Stored as trip.planning_preferences, so every
    planner prompt and the trip document see it without extra plumbing. All optional."""
    pace: Literal["relaxed", "balanced", "packed"] = "balanced"
    interests: List[str] = Field(default_factory=list, max_length=3)
    avoid: List[str] = Field(default_factory=list, max_length=3)
    start_date: Optional[date] = None
    adults: int = Field(default=1, ge=1, le=20)
    children: int = Field(default=0, ge=0, le=20)
    comfort: Literal["budget", "mid_range", "comfortable"] = "mid_range"
    travel_mode: Literal["transit", "walk", "taxi", "drive"] = "transit"
    guide: Optional[str] = Field(default=None, pattern=r"^[a-z0-9-]{1,32}$")
    # The traveler's own currency, for "≈" conversions next to trip amounts (enrichment's exchange rates).
    home_currency: Optional[Literal["INR", "JPY", "USD", "EUR", "GBP", "AUD", "CAD"]] = None

    @field_validator("interests", "avoid")
    @classmethod
    def clean_preferences(cls, values: List[str]) -> List[str]:
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 80 for value in cleaned):
            raise ValueError("Each preference must be between 1 and 80 characters.")
        return list(dict.fromkeys(cleaned))


class OnboardingFormSubmission(BaseModel):
    origin: str = Field(min_length=1, max_length=255)
    destination: str = Field(min_length=1, max_length=255)
    duration_days: int = Field(ge=1, le=365)
    places_to_visit: List[str] = Field(default_factory=list, max_length=20)
    planning_preferences: PlanningPreferences = Field(default_factory=PlanningPreferences)
    # Kept in the budget ledger (target + trip currency), not in the brief.
    budget_amount: Optional[float] = Field(default=None, gt=0, le=1_000_000_000)
    currency: Optional[Literal["INR", "JPY", "USD", "EUR", "GBP", "AUD", "CAD"]] = None

    @field_validator("origin", "destination")
    @classmethod
    def clean_place(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Location cannot be blank.")
        return value

    @field_validator("places_to_visit")
    @classmethod
    def clean_places(cls, values: List[str]) -> List[str]:
        cleaned = [value.strip() for value in values]
        if any(not value or len(value) > 255 for value in cleaned):
            raise ValueError("Each place must be between 1 and 255 characters.")
        return list(dict.fromkeys(cleaned))


class ConversationMessageListResponse(BaseModel):
    messages: List[ConversationMessageResponse]


class RouteLegInput(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    from_lat: float = Field(ge=-90, le=90)
    from_lon: float = Field(ge=-180, le=180)
    to_lat: float = Field(ge=-90, le=90)
    to_lon: float = Field(ge=-180, le=180)


class RouteMetricsRequest(BaseModel):
    legs: List[RouteLegInput] = Field(max_length=20)


class GeocodePlaceInput(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    is_origin: bool = False


class GeocodeRequest(BaseModel):
    places: List[GeocodePlaceInput] = Field(max_length=20)


class NearbyPlacesRequest(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
