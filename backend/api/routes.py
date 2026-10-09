"""HTTP + WebSocket routes. Thin adapters over TurnOrchestrator: no business logic here."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import ValidationError

from backend.agents.orchestrator import TurnOrchestrator
from backend.agents.plan_ops import PlanRequestError
from backend.api.deps import get_container, get_orchestrator, ws_orchestrator
from backend.api.schemas import (
    ChatRequest,
    ConfirmRequest,
    CreateSessionRequest,
    ErrorResponse,
    FeedbackRequest,
    FeedbackResponse,
    HealthResponse,
    SessionView,
    UpdateContextRequest,
)
from backend.container import Container
from backend.memory.session import SessionNotFound
from backend.schemas.trip_plan import TripPlan
from backend.schemas.turn import TurnEvent, TurnEventType, TurnResult

router = APIRouter(prefix="/api")

Orchestrator = Annotated[TurnOrchestrator, Depends(get_orchestrator)]
NOT_FOUND = {404: {"model": ErrorResponse}}


@router.get("/health", response_model=HealthResponse, tags=["meta"])
async def health(container: Annotated[Container, Depends(get_container)]) -> HealthResponse:
    s = container.settings
    return HealthResponse(
        storage_backend=s.storage_backend,
        router_provider=s.router_provider,
        agent_llm_provider=s.agent_llm_provider,
        langfuse_enabled=s.langfuse_enabled,
    )


@router.post(
    "/sessions", response_model=SessionView, status_code=status.HTTP_201_CREATED, tags=["sessions"]
)
async def create_session(body: CreateSessionRequest, orchestrator: Orchestrator) -> SessionView:
    return SessionView.of(await orchestrator.create_session(body.context))


@router.get("/sessions/{session_id}", response_model=SessionView, responses=NOT_FOUND, tags=["sessions"])
async def get_session(session_id: str, orchestrator: Orchestrator) -> SessionView:
    return SessionView.of(await orchestrator.get_session(session_id))


@router.put(
    "/sessions/{session_id}/context", response_model=SessionView, responses=NOT_FOUND, tags=["sessions"]
)
async def update_context(
    session_id: str, body: UpdateContextRequest, orchestrator: Orchestrator
) -> SessionView:
    return SessionView.of(await orchestrator.update_context(session_id, body.context))


@router.post(
    "/sessions/{session_id}/messages",
    response_model=TurnResult,
    responses={**NOT_FOUND, 409: {"model": ErrorResponse}},
    tags=["chat"],
)
async def post_message(session_id: str, body: ChatRequest, orchestrator: Orchestrator) -> TurnResult:
    """409 when `focus` is sent without a plan; 422 when `focus` names a part not in the plan."""
    return await orchestrator.handle_turn(session_id, body.message, focus=body.focus)


@router.post(
    "/sessions/{session_id}/plan/confirm",
    response_model=TripPlan,
    responses={**NOT_FOUND, 409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    tags=["plan"],
)
async def confirm_items(session_id: str, body: ConfirmRequest, orchestrator: Orchestrator) -> TripPlan:
    try:
        return await orchestrator.confirm(
            session_id,
            item_ids=body.item_ids,
            ticket_ids=body.ticket_ids,
            hotel=body.hotel,
            confirmed=body.confirmed,
        )
    except SessionNotFound:
        raise
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/sessions/{session_id}/plan/feedback",
    response_model=FeedbackResponse,
    responses={**NOT_FOUND, 409: {"model": ErrorResponse}},
    tags=["plan"],
)
async def plan_feedback(
    session_id: str, body: FeedbackRequest, orchestrator: Orchestrator
) -> FeedbackResponse:
    queued = await orchestrator.mark_useful(session_id, useful=body.useful, rating=body.rating)
    return FeedbackResponse(queued=queued)


@router.websocket("/sessions/{session_id}/stream")
async def stream(websocket: WebSocket, session_id: str) -> None:
    """Client sends {"message": "...", "focus": {"kind": ..., "id": ...} | null}; server streams
    TurnEvent JSON objects, ending each turn with a "done" event that carries the full TurnResult.
    A message refused before its turn starts (invalid JSON, unknown session, a focus the plan
    cannot serve) gets one "error" event with an empty trace_id and no "done"."""
    orchestrator = ws_orchestrator(websocket)
    await websocket.accept()

    async def emit(event: TurnEvent) -> None:
        await websocket.send_text(event.model_dump_json())

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                request = ChatRequest.model_validate_json(raw)
            except ValidationError:
                await emit(TurnEvent(type=TurnEventType.ERROR, trace_id="", message="invalid message"))
                continue
            try:
                await orchestrator.handle_turn(session_id, request.message, emit=emit, focus=request.focus)
            except SessionNotFound:
                await emit(TurnEvent(type=TurnEventType.ERROR, trace_id="", message="session not found"))
            except PlanRequestError as exc:
                await emit(TurnEvent(type=TurnEventType.ERROR, trace_id="", message=exc.message))
    except WebSocketDisconnect:
        return
