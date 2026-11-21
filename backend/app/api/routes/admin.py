"""Administrator-only endpoints.

The admin flag lives on the local account (models/user.py). The settings here
are the gate and the instructions the graphs already read. A writer key
pasted in Settings is stored encrypted and used on the next turn.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser, get_current_user
from app.core.database import get_db
from app.models.user import User
from app.services.admin_settings import PROMPTS, prompt_rows, reset_prompt, save_prompt, save_router, router_view

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _defaults() -> dict[str, str]:
    from app.agents.trip_planner.copilot.graph import RESPOND_INSTRUCTION
    from app.agents.trip_planner.nodes.planning_trip_node import (
        ANSWER_SYSTEM_INSTRUCTION,
        PLAN_GENERATION_SYSTEM_INSTRUCTION,
        REVISION_SYSTEM_INSTRUCTION,
    )
    from app.agents.trip_planner.nodes.understand_user_msg_node import UNDERSTAND_USER_MSG_NODE_SYSTEM_INSTRUCTION
    return {
        "plan": PLAN_GENERATION_SYSTEM_INSTRUCTION,
        "revise": REVISION_SYSTEM_INSTRUCTION,
        "answer": ANSWER_SYSTEM_INSTRUCTION,
        "copilot_reply": RESPOND_INSTRUCTION,
        "understand": UNDERSTAND_USER_MSG_NODE_SYSTEM_INSTRUCTION,
    }


class RouterSettingsIn(BaseModel):
    router_threshold: float | None = Field(default=None, ge=0.5, le=0.99)
    rules_first: bool | None = None
    decision_enabled: bool | None = None
    router_enabled: bool | None = None


class PromptIn(BaseModel):
    body: str = Field(min_length=1, max_length=20000)


async def require_admin(current_user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    """Reject anyone whose token does not carry the admin flag."""
    if not current_user.user_metadata.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required.",
        )
    return current_user


@router.get("/users")
async def list_users(
    _admin: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List the app's local accounts."""
    accounts = (await db.execute(select(User).order_by(User.created_at))).scalars().all()
    return {
        "count": len(accounts),
        "users": [
            {
                "id": account.id,
                "email": account.email,
                "full_name": account.full_name,
                "is_admin": account.is_admin,
                "created_at": account.created_at.isoformat() if account.created_at else None,
            }
            for account in accounts
        ],
    }


@router.get("/settings")
async def get_settings(_admin: AuthenticatedUser = Depends(require_admin)):
    """The gate the next turn will use, and whether Jev has a URL and key."""
    return router_view()


@router.put("/settings")
async def put_settings(
    body: RouterSettingsIn,
    _admin: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    return await save_router(
        db,
        threshold=body.router_threshold,
        rules_first=body.rules_first,
        decision_enabled=body.decision_enabled,
        router_enabled=body.router_enabled,
    )


@router.get("/prompts")
async def get_prompts(_admin: AuthenticatedUser = Depends(require_admin)):
    return {"prompts": prompt_rows(_defaults())}


@router.put("/prompts/{name}")
async def put_prompt(
    name: str,
    body: PromptIn,
    _admin: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if name not in PROMPTS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown prompt.")
    await save_prompt(db, name, body.body)
    return {"name": name, "override": body.body.strip()}


@router.delete("/prompts/{name}")
async def delete_prompt(
    name: str,
    _admin: AuthenticatedUser = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    if name not in PROMPTS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown prompt.")
    await reset_prompt(db, name)
    return {"name": name, "override": ""}


class DeskSayIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class DeskStopIn(BaseModel):
    day: int = Field(ge=1, le=60)
    name: str = Field(min_length=1, max_length=255)
    start: str = Field(pattern=r"^\d{2}:\d{2}$")


def _desk_row(trip) -> dict:
    from app.desk.policy import desk_state
    desk = desk_state(trip.planning_preferences)
    if desk["status"] == "queued":
        lane = "waiting"
    elif desk["status"] == "handling":
        lane = "handling"
    elif trip.status.value == "COMPLETED":
        lane = "history"
    else:
        lane = "open"
    return {
        "trip_id": str(trip.id),
        "destination": trip.destination,
        "origin": trip.origin_text,
        "trip_status": trip.status.value,
        "status": desk["status"],
        "lane": lane,
        "reason": desk["reason"],
        "stuck": desk["stuck"],
        "guest": bool(trip.guest_id) and not trip.user_id,
        "updated_at": trip.updated_at.isoformat() if trip.updated_at else None,
    }


@router.get("/desk")
async def desk_queue(_admin: AuthenticatedUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Every recent chat, split later into waiting, handling, open, and history."""
    from app.models.trip import Trip
    trips = (await db.execute(select(Trip).order_by(Trip.updated_at.desc()).limit(80))).scalars().all()
    return {"chats": [_desk_row(trip) for trip in trips]}


class DeskBriefIn(BaseModel):
    origin: str | None = Field(default=None, max_length=255)
    destination: str | None = Field(default=None, max_length=255)
    duration_days: int | None = Field(default=None, ge=1, le=60)
    currency: str | None = Field(default=None, min_length=3, max_length=3)


async def _trip_or_404(db: AsyncSession, trip_id: UUID):
    from app.models.trip import Trip
    trip = await db.get(Trip, trip_id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found.")
    return trip


@router.get("/desk/{trip_id}")
async def desk_chat(trip_id: UUID, _admin: AuthenticatedUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """The open chat, the brief, and the current day plan. The planner graph is not called."""
    from app.desk.activity import read_planner_run
    from app.models.enums import MessageRole
    from app.models.trip import ConversationMessage, ConversationSession
    trip = await _trip_or_404(db, trip_id)
    result = await db.execute(
        select(ConversationMessage)
        .join(ConversationSession, ConversationMessage.session_id == ConversationSession.id)
        .where(ConversationSession.trip_id == trip_id)
        .order_by(ConversationMessage.created_at.asc())
    )
    messages = []
    day_plan = None
    previous_at = None
    for message in result.scalars():
        payload = message.payload or {}
        if message.role == MessageRole.USER:
            speaker = "traveller"
        elif payload.get("speaker") == "person" or payload.get("kind") == "DESK":
            speaker = "person"
        else:
            speaker = "planner"
        from app.security.pii import mask_pii
        shown = mask_pii(message.content or "") if speaker == "traveller" else (message.content or "")
        created = message.created_at
        latency_ms = None
        if speaker == "planner" and previous_at is not None and created is not None:
            latency_ms = max(0, int((created - previous_at).total_seconds() * 1000))
        if created is not None:
            previous_at = created
        messages.append({
            "id": str(message.id),
            "speaker": speaker,
            "content": shown,
            "created_at": created.isoformat() if created else None,
            "latency_ms": latency_ms,
        })
        if isinstance(payload.get("day_plan"), list):
            day_plan = payload["day_plan"]
    prefs = trip.planning_preferences or {}
    return {
        "chat": _desk_row(trip),
        "brief": {
            "origin": trip.origin_text or "",
            "destination": trip.destination or "",
            "duration_days": trip.duration_days,
            "currency": trip.currency,
            "pace": prefs.get("pace") or "",
            "travel_mode": prefs.get("travel_mode") or "",
        },
        "messages": messages[-80:],
        "day_plan": day_plan or [],
        "activity": read_planner_run(str(trip_id)),
    }


@router.put("/desk/{trip_id}/brief")
async def desk_brief(
    trip_id: UUID, body: DeskBriefIn,
    _admin: AuthenticatedUser = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    """Save the trip details. This does not start the planner."""
    trip = await _trip_or_404(db, trip_id)
    if body.origin is not None:
        trip.origin_text = body.origin.strip() or None
    if body.destination is not None:
        trip.destination = body.destination.strip() or None
    if body.duration_days is not None:
        trip.duration_days = body.duration_days
    if body.currency:
        trip.currency = body.currency.strip().upper()
    await db.commit()
    return _desk_row(trip)


@router.post("/desk/{trip_id}/release-draft")
async def desk_release_draft(trip_id: UUID, _admin: AuthenticatedUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Hand the chat back and leave the plan editable."""
    from app.desk.policy import release
    from app.models.enums import MessageRole, MessageType, TripStatus
    from app.repositories.conversation import ConversationRepository
    from app.repositories.message import MessageRepository
    trip = await _trip_or_404(db, trip_id)
    trip.planning_preferences = {**(trip.planning_preferences or {}), "desk": release({})}
    if trip.status == TripStatus.COMPLETED:
        trip.status = TripStatus.PLANNING
    session = await ConversationRepository().get_or_create_active_session(db, trip.id)
    text = "The plan is back with you. You can edit it in the planner."
    await MessageRepository().create_message(
        db, session_id=session.id, role=MessageRole.ASSISTANT,
        message_type=MessageType.TEXT, content=text, payload={"kind": "DESK", "speaker": "person"},
    )
    await db.commit()
    return _desk_row(trip)


@router.post("/desk/{trip_id}/take")
async def desk_take(trip_id: UUID, _admin: AuthenticatedUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    from app.desk.policy import desk_state, hold
    from app.models.trip import Trip
    trip = await db.get(Trip, trip_id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found.")
    desk = hold(desk_state(trip.planning_preferences))
    trip.planning_preferences = {**(trip.planning_preferences or {}), "desk": desk}
    await db.commit()
    return _desk_row(trip)


@router.post("/desk/{trip_id}/release")
async def desk_release(trip_id: UUID, _admin: AuthenticatedUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    from app.desk.policy import release
    from app.models.trip import Trip
    trip = await db.get(Trip, trip_id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found.")
    trip.planning_preferences = {**(trip.planning_preferences or {}), "desk": release({})}
    await db.commit()
    return _desk_row(trip)


@router.post("/desk/{trip_id}/say")
async def desk_say(
    trip_id: UUID, body: DeskSayIn,
    _admin: AuthenticatedUser = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    """A person's reply. The planner graph is not called."""
    from app.models.enums import MessageRole, MessageType
    from app.models.trip import Trip
    from app.repositories.conversation import ConversationRepository
    from app.repositories.message import MessageRepository
    trip = await db.get(Trip, trip_id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found.")
    session = await ConversationRepository().get_or_create_active_session(db, trip.id)
    message = await MessageRepository().create_message(
        db, session_id=session.id, role=MessageRole.ASSISTANT,
        message_type=MessageType.TEXT, content=body.text, payload={"kind": "DESK", "speaker": "person"},
    )
    await db.commit()
    return {"content": message.content, "payload": message.payload}


@router.post("/desk/{trip_id}/stop")
async def desk_stop(
    trip_id: UUID, body: DeskStopIn,
    _admin: AuthenticatedUser = Depends(require_admin), db: AsyncSession = Depends(get_db),
):
    """Move one stop on the open plan and keep every other stop."""
    from sqlalchemy.orm.attributes import flag_modified
    from app.models.enums import MessageRole
    from app.models.trip import ConversationMessage, ConversationSession, Trip
    from app.planning.upgrades import move_stop
    trip = await db.get(Trip, trip_id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found.")
    result = await db.execute(
        select(ConversationMessage)
        .join(ConversationSession, ConversationMessage.session_id == ConversationSession.id)
        .where(ConversationSession.trip_id == trip_id, ConversationMessage.role == MessageRole.ASSISTANT)
        .order_by(ConversationMessage.created_at.desc())
    )
    for message in result.scalars():
        payload = dict(message.payload or {})
        days = payload.get("day_plan")
        if not isinstance(days, list):
            continue
        try:
            payload["day_plan"] = move_stop(days, day=body.day, name=body.name, start=body.start)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        payload["kind"] = payload.get("kind") or "ITINERARY_GRAPH"
        message.payload = payload
        flag_modified(message, "payload")
        await db.commit()
        return {"day_plan": payload["day_plan"]}
    raise HTTPException(status_code=404, detail="This trip has no day plan to edit.")


@router.get("/eval")
async def eval_cases(_admin: AuthenticatedUser = Depends(require_admin)):
    """Offline cases for the upgrades. They call the same functions a turn uses."""
    from app.desk.suite import offline_cases
    cases = offline_cases()
    return {"passed": sum(1 for case in cases if case["passed"]), "total": len(cases), "cases": cases}


@router.get("/errors")
async def error_chats(_admin: AuthenticatedUser = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Trips a traveller marked not useful."""
    from app.models.trip import Trip
    trips = (await db.execute(select(Trip).order_by(Trip.updated_at.desc()))).scalars().all()
    rows = []
    for trip in trips:
        useful = (trip.planning_preferences or {}).get("useful")
        if useful is False:
            rows.append({"trip_id": str(trip.id), "destination": trip.destination, "useful": False})
    return {"count": len(rows), "chats": rows}
