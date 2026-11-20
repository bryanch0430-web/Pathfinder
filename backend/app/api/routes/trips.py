import uuid
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser, RequestIdentity, get_current_user, get_request_identity
from app.core.database import get_db
from app.schemas.trip import (
    ConversationMessageListResponse,
    SendMessageRequest,
    TripCreateResponse,
    TripResponse,
    TripStateResponse,
    RouteMetricsRequest,
    GeocodeRequest,
    NearbyPlacesRequest,
)
from app.schemas.budget import BudgetItemInput, BudgetLockRequest, BudgetResponse, BudgetSettingsUpdate
from app.schemas.trip_document import TripDocument
from app.services.trip_document import build_trip_document
from app.services.budget import (
    accept_budget_estimates, add_budget_item, budget_response, delete_budget_item, estimate_budget,
    update_budget_item,
    update_budget_settings,
)
from app.services.conversation import conversation_service
from app.services.google_places import get_nearby_places
from app.services.place_geocoding import geocode_places
from app.services.route_metrics import get_route_metrics
from app.services.trip import trip_service

router = APIRouter(prefix="/api/trips", tags=["trips"])


class TripFeedbackIn(BaseModel):
    useful: bool


async def _owned_trip(db: AsyncSession, trip_id: uuid.UUID, identity: RequestIdentity):
    trip = await trip_service.trip_repo.get_by_id(db, trip_id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found.")
    trip_service.verify_trip_ownership(trip, identity)
    return trip


@router.get("/me", response_model=List[TripResponse])
async def get_my_trips(
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve all trips associated with the authenticated user."""
    return await trip_service.get_user_trips(db, current_user.id)


@router.get("", response_model=List[TripResponse])
async def list_trips(
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve all trips associated with current identity (authenticated user or guest)."""
    if identity.user_id:
        return await trip_service.get_user_trips(db, identity.user_id)
    elif identity.guest_id:
        return await trip_service.get_guest_trips(db, identity.guest_id)
    return []


@router.post("", response_model=TripCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_trip(
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    """Initialize a new conversational trip session belonging to either user_id or guest_id."""
    return await trip_service.create_trip(db, identity=identity)


@router.post("/{trip_id}/messages", response_model=TripStateResponse)
async def send_trip_message(
    trip_id: uuid.UUID,
    request: SendMessageRequest,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    """Process a conversational user message or UI action for an authorized trip."""
    return await conversation_service.process_message(db, trip_id, request, identity=identity)


@router.post("/{trip_id}/messages/stream")
async def send_trip_message_stream(
    trip_id: uuid.UUID,
    request: SendMessageRequest,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    """Process message and stream tokens via Server-Sent Events (SSE)."""
    # 1. Pre-validate trip existence and ownership before initiating SSE stream
    trip = await trip_service.trip_repo.get_by_id(db, trip_id)
    if not trip:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trip with ID '{trip_id}' not found.",
        )
    trip_service.verify_trip_ownership(trip, identity)

    return StreamingResponse(
        conversation_service.process_message_stream(trip_id=trip_id, request=request, identity=identity),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{trip_id}", response_model=TripStateResponse)
async def get_trip(
    trip_id: uuid.UUID,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve current trip state and active conversation session after authorization check."""
    return await trip_service.get_trip(db, trip_id, identity=identity)


@router.get("/{trip_id}/messages", response_model=ConversationMessageListResponse)
async def get_trip_messages(
    trip_id: uuid.UUID,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    """Retrieve ordered conversation messages for an authorized trip."""
    return await conversation_service.get_trip_messages(db, trip_id, identity=identity)


@router.get("/{trip_id}/budget", response_model=BudgetResponse)
async def get_trip_budget(
    trip_id: uuid.UUID,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    """Read the itemized ledger, reconciling it with the latest saved itinerary graph."""
    trip = await _owned_trip(db, trip_id, identity)
    response = await budget_response(db, trip)
    await db.commit()
    return response


@router.put("/{trip_id}/budget/settings", response_model=BudgetResponse)
async def put_trip_budget_settings(
    trip_id: uuid.UUID, request: BudgetSettingsUpdate,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    trip = await _owned_trip(db, trip_id, identity)
    return await update_budget_settings(db, trip, request)


@router.post("/{trip_id}/budget/items", response_model=BudgetResponse, status_code=201)
async def post_trip_budget_item(
    trip_id: uuid.UUID, request: BudgetItemInput,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    trip = await _owned_trip(db, trip_id, identity)
    return await add_budget_item(db, trip, request)


@router.put("/{trip_id}/budget/items/{item_id}", response_model=BudgetResponse)
async def put_trip_budget_item(
    trip_id: uuid.UUID, item_id: uuid.UUID, request: BudgetItemInput,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    trip = await _owned_trip(db, trip_id, identity)
    return await update_budget_item(db, trip, item_id, request)


@router.delete("/{trip_id}/budget/items/{item_id}", response_model=BudgetResponse)
async def remove_trip_budget_item(
    trip_id: uuid.UUID, item_id: uuid.UUID,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    trip = await _owned_trip(db, trip_id, identity)
    return await delete_budget_item(db, trip, item_id)


@router.get("/{trip_id}/calendar.ics")
async def trip_calendar(
    trip_id: uuid.UUID,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    """Stops, stays and route legs as an .ics file. A later import of the same trip updates the events."""
    from app.routing.calendar_ics import document_to_ics

    trip = await _owned_trip(db, trip_id, identity)
    document = await build_trip_document(db, trip)
    await db.commit()
    body = document_to_ics(document)
    if not body:
        raise HTTPException(status_code=409, detail="This trip needs a start date before it can go on a calendar.")
    name = re_slug(document.destination or "trip")
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.ics"'},
    )


def re_slug(value: str) -> str:
    import re
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return cleaned or "trip"


@router.get("/{trip_id}/document", response_model=TripDocument)
async def get_trip_document(
    trip_id: uuid.UUID,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    """One normalized trip for the studio, sketchbook and exports, whichever planner built it."""
    trip = await _owned_trip(db, trip_id, identity)
    document = await build_trip_document(db, trip)
    await db.commit()  # the budget part reconciles ledger rows, like GET /budget
    return document


@router.post("/{trip_id}/finalize", response_model=TripResponse)
async def finalize_trip(
    trip_id: uuid.UUID,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    trip = await _owned_trip(db, trip_id, identity)
    return await trip_service.finalize_trip(db, trip)


@router.post("/{trip_id}/budget/estimates", response_model=BudgetResponse)
async def post_trip_budget_estimates(
    trip_id: uuid.UUID,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    """Suggest per-row amounts (build-with-agent numbers first, then traveler-sourced research)."""
    trip = await _owned_trip(db, trip_id, identity)
    return await estimate_budget(db, trip)


@router.post("/{trip_id}/budget/estimates/accept", response_model=BudgetResponse)
async def post_accept_trip_budget_estimates(
    trip_id: uuid.UUID,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    """Copy suggestions into every row that has no traveler-entered amount yet."""
    trip = await _owned_trip(db, trip_id, identity)
    return await accept_budget_estimates(db, trip)


@router.post("/{trip_id}/budget/lock", response_model=BudgetResponse)
async def post_lock_trip_budget(
    trip_id: uuid.UUID, request: BudgetLockRequest,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    trip = await _owned_trip(db, trip_id, identity)
    from app.services.budget import lock_budget
    return await lock_budget(db, trip, accept_suggestions=request.accept_suggestions)


@router.post("/{trip_id}/budget/unlock", response_model=BudgetResponse)
async def post_unlock_trip_budget(
    trip_id: uuid.UUID,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    trip = await _owned_trip(db, trip_id, identity)
    from app.services.budget import unlock_budget
    return await unlock_budget(db, trip)


@router.post("/{trip_id}/route-metrics")
async def route_metrics(
    trip_id: uuid.UUID,
    request: RouteMetricsRequest,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    """Get optional road distance and duration for geocoded itinerary legs."""
    trip = await trip_service.trip_repo.get_by_id(db, trip_id)
    if not trip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found.")
    trip_service.verify_trip_ownership(trip, identity)
    return await get_route_metrics(request)


@router.post("/{trip_id}/geocode")
async def geocode_trip_places(
    trip_id: uuid.UUID,
    request: GeocodeRequest,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    """Resolve unresolved graph places without exposing the ORS key to the browser."""
    trip = await trip_service.trip_repo.get_by_id(db, trip_id)
    if not trip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found.")
    trip_service.verify_trip_ownership(trip, identity)
    return await geocode_places(request, trip.destination)


@router.post("/{trip_id}/nearby-places")
async def nearby_trip_places(
    trip_id: uuid.UUID,
    request: NearbyPlacesRequest,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    """Return live nearby points of interest for an authorized trip."""
    trip = await trip_service.trip_repo.get_by_id(db, trip_id)
    if not trip:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Trip not found.")
    trip_service.verify_trip_ownership(trip, identity)
    return await get_nearby_places(request, str(trip_id))


@router.post("/{trip_id}/itinerary-graph")
async def itinerary_graph(
    trip_id: uuid.UUID,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    """Backfill the graph of an existing, authorized completed itinerary once."""
    return await conversation_service.get_or_create_itinerary_graph(db, trip_id, identity=identity)


@router.post("/{trip_id}/feedback")
async def trip_feedback(
    trip_id: uuid.UUID,
    body: TripFeedbackIn,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    """Remember whether this traveller kept the plan. It does not train a later trip."""
    return await conversation_service.mark_useful(db, trip_id, identity, body.useful)


@router.post("/{trip_id}/undo")
async def undo_trip_plan(
    trip_id: uuid.UUID,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    """Put the previous saved plan back. One step only."""
    message = await conversation_service.undo_last_plan(db, trip_id, identity)
    return {"content": message.content, "payload": message.payload}


@router.delete("/{trip_id}")
async def delete_trip(
    trip_id: uuid.UUID,
    identity: RequestIdentity = Depends(get_request_identity),
    db: AsyncSession = Depends(get_db),
):
    """Delete a trip and its associated conversation sessions/messages."""
    return await trip_service.delete_trip(db, trip_id, identity=identity)

