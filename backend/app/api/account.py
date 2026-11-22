"""Accounts, traveller memory and the saved-trip wishlist."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import (
    bearer_user,
    container,
    identity_for,
    issue_token,
    password_hash,
    password_ok,
    presented_token,
    require_session,
    user_public,
)
from app.api.views import load_context, session_view
from app.domain.models import ChatMessage, TripContext

router = APIRouter()


def _auth_payload(settings, user: dict[str, Any]) -> dict[str, Any]:
    token = issue_token(settings, user["id"])
    return {"token": token, "token_type": "bearer", "profile_token": token, "user": user_public(user)}


@router.post("/api/account/signup")
async def signup(request: Request) -> dict[str, Any]:
    c = container(request)
    body = await request.json()
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    if "@" not in email or len(password) < 6:
        raise HTTPException(status_code=400, detail="An email and a password of at least 6 characters are needed.")
    try:
        user = await c.store.create_user(email, password_hash(password), str(body.get("display_name", "")).strip())
    except ValueError:
        raise HTTPException(status_code=409, detail="An account with that email already exists.") from None
    session_id = body.get("session_id")
    if session_id:
        session = await c.store.get_session(session_id)
        guest = str(body.get("client_token", ""))
        if session and (not session.get("client_token") or session.get("client_token") == guest):
            await c.store.claim_session(session_id, user["id"])
    return _auth_payload(c.settings, user)


@router.post("/api/account/signin")
async def signin(request: Request) -> dict[str, Any]:
    c = container(request)
    body = await request.json()
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    user = await c.store.get_user_by_email(email)
    if user is None or not password_ok(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="That email and password do not match an account.")
    session_id = body.get("session_id")
    if session_id:
        session = await c.store.get_session(session_id)
        guest = str(body.get("client_token", ""))
        if session and (not session.get("client_token") or session.get("client_token") == guest):
            await c.store.claim_session(session_id, user["id"])
    return _auth_payload(c.settings, user)


@router.get("/api/account/whoami")
async def whoami(request: Request) -> dict[str, Any]:
    c = container(request)
    user_id = bearer_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Not signed in.")
    user = await c.store.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in.")
    return {"user": user_public(user), "profile_token": issue_token(c.settings, user_id)}


@router.post("/api/account/attach")
async def attach(request: Request) -> dict[str, Any]:
    c = container(request)
    user_id = bearer_user(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="Sign in first.")
    body = await request.json()
    session_id = str(body.get("session_id", ""))
    guest = str(body.get("client_token", ""))
    session = await c.store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="That trip no longer exists.")
    if session.get("client_token") and session["client_token"] != guest:
        raise HTTPException(status_code=403, detail="That trip belongs to another traveller.")
    await c.store.claim_session(session_id, user_id)
    return {"claimed": True}


@router.get("/api/traveller")
async def traveller(request: Request, client_token: str = "") -> dict[str, Any]:
    c = container(request)
    identity = identity_for(request, client_token)
    if not identity:
        return {
            "client_token": "",
            "preferences": [],
            "hard_constraints": [],
            "sessions": 0,
            "saved_trips": 0,
            "destinations": [],
            "updated_at": None,
        }
    profile = await c.store.get_profile(identity) or {"preferences": [], "hard_constraints": [], "updated_at": None}
    wishlist = await c.store.list_wishlist(identity)
    sessions = await c.store.list_sessions(200)
    destinations = sorted({row.get("destination", "") for row in wishlist if row.get("destination")})
    return {
        "client_token": client_token or presented_token(request),
        "preferences": profile["preferences"],
        "hard_constraints": profile["hard_constraints"],
        "sessions": len([row for row in sessions if (row.get("client_token") or "") == presented_token(request)]),
        "saved_trips": len(wishlist),
        "destinations": destinations,
        "updated_at": profile.get("updated_at"),
    }


@router.patch("/api/traveller")
async def patch_traveller(request: Request) -> dict[str, Any]:
    c = container(request)
    body = await request.json()
    identity = identity_for(request, body.get("client_token"))
    if not identity:
        raise HTTPException(status_code=400, detail="A traveller token is needed.")
    preferences = [str(value)[:80] for value in (body.get("preferences") or [])][:24]
    profile = await c.store.get_profile(identity) or {"hard_constraints": []}
    saved = await c.store.save_profile(identity, preferences, profile.get("hard_constraints", []))
    return {
        "profile": {
            "client_token": body.get("client_token", ""),
            "preferences": saved["preferences"],
            "hard_constraints": saved["hard_constraints"],
            "sessions": 0,
            "saved_trips": len(await c.store.list_wishlist(identity)),
            "destinations": [],
            "updated_at": saved["updated_at"],
        }
    }


@router.post("/api/recall/clear")
async def clear_memory(request: Request) -> dict[str, Any]:
    c = container(request)
    body = await request.json()
    identity = identity_for(request, body.get("client_token"))
    if not identity:
        return {"cleared": False, "saved_trips": 0, "preferences": 0}
    wishlist = await c.store.list_wishlist(identity)
    return {"cleared": True, "saved_trips": len(wishlist), "preferences": 0}


@router.get("/api/wishlist")
async def list_wishlist(request: Request) -> dict[str, Any]:
    c = container(request)
    identity = identity_for(request)
    rows = await c.store.list_wishlist(identity) if identity else []
    trips = []
    for row in rows:
        body = _body(row)
        trips.append(
            {
                "id": row["id"],
                "trip_id": body.get("trip_id") or row["id"],
                "destination": row.get("destination", ""),
                "rating": row.get("stars") or 0,
                "stars": row.get("stars"),
                "summary": row.get("summary", ""),
                "created_at": row.get("created_at", ""),
                "days": len(body.get("days") or []),
            }
        )
    return {"trips": trips}


@router.get("/api/wishlist/{saved_id}")
async def get_wishlist(saved_id: str, request: Request, client_token: str = "") -> dict[str, Any]:
    c = container(request)
    row = await c.store.get_wishlist(saved_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That saved trip is not there.")
    identity = identity_for(request, client_token)
    if row.get("identity") and row["identity"] != identity:
        raise HTTPException(status_code=403, detail="That saved trip belongs to another traveller.")
    body = _body(row)
    return {
        "id": row["id"],
        "destination": row.get("destination", ""),
        "summary": row.get("summary", ""),
        "stars": row.get("stars"),
        "days": [
            {
                "date": day.get("date", ""),
                "title": day.get("title", ""),
                "items": [item.get("name", "") for item in day.get("items", [])],
            }
            for day in (body.get("days") or [])
        ],
    }


@router.post("/api/wishlist/{saved_id}/open")
async def open_wishlist(saved_id: str, request: Request) -> dict[str, Any]:
    c = container(request)
    row = await c.store.get_wishlist(saved_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That saved trip is not there.")
    plan = _body(row)
    token = presented_token(request)
    user_id = bearer_user(request)
    session = await c.store.create_session(client_token=token, user_id=user_id)
    from app.domain.models import TripPlan

    try:
        copy = TripPlan(**plan)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"That saved trip is unreadable: {exc}") from exc
    copy.session_id = session["id"]
    copy.version = 1
    await c.store.save_plan(copy.model_dump(mode="json"))
    context = TripContext(
        session_id=session["id"],
        destination=copy.destination.name,
        origin=copy.origin,
        start_date=copy.dates.start,
        end_date=copy.dates.end,
        days=len(copy.days),
        party_size=copy.party_size,
        budget=copy.budget.amount if copy.budget else None,
        currency=copy.budget.currency if copy.budget else c.settings.default_currency,
    )
    await c.store.save_context(session["id"], context.model_dump())
    session_row = await c.store.get_session(session["id"])
    return await session_view(c, session["id"], session_row)


def _body(row: dict[str, Any]) -> dict[str, Any]:
    import json

    try:
        return json.loads(row.get("body") or "{}")
    except ValueError:
        return {}
