"""HTTP request/response models. Domain models (TripPlan, TurnResult, ...) come from
backend.schemas so the OpenAPI document and the shared JSON Schema have one source of truth."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from backend.schemas.common import StrictModel
from backend.schemas.memory import ConversationTurn, PreferenceProfile, SessionState
from backend.schemas.trip import TripContext
from backend.schemas.trip_plan import TripPlan


class CreateSessionRequest(StrictModel):
    context: TripContext = Field(default_factory=TripContext)


class UpdateContextRequest(StrictModel):
    context: TripContext


class SessionView(StrictModel):
    session_id: str
    context: TripContext
    plan: TripPlan | None
    history: list[ConversationTurn]
    preferences: PreferenceProfile

    @classmethod
    def of(cls, state: SessionState) -> SessionView:
        return cls(
            session_id=state.session_id,
            context=state.context,
            plan=state.plan,
            history=state.history,
            preferences=state.preferences,
        )


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=4000)


class ConfirmRequest(StrictModel):
    item_ids: list[str] = Field(default_factory=list)
    ticket_ids: list[str] = Field(default_factory=list)
    hotel: bool | None = None
    confirmed: bool = True


class FeedbackRequest(StrictModel):
    useful: bool
    rating: int = Field(ge=1, le=5)


class FeedbackResponse(StrictModel):
    queued: bool


class HealthResponse(StrictModel):
    status: Literal["ok"] = "ok"
    storage_backend: str
    router_provider: str
    agent_llm_provider: str
    langfuse_enabled: bool


class ErrorResponse(StrictModel):
    detail: str
