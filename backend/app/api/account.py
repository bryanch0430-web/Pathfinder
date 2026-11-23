"""Accounts, traveller memory and the saved-trip wishlist."""

from __future__ import annotations

import hmac
import secrets
import time
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


MAX_SIGNIN_FAILURES = 10
SIGNIN_WINDOW_SECONDS = 600.0


async def _json_object(request: Request, *, empty_ok: bool = False) -> dict[str, Any]:
    """The JSON object body, or a clean 400 (never a 500) when it is missing or malformed."""
    raw = await request.body()
    if empty_ok and not raw.strip():
        return {}
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="The request body must be a JSON object.") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="The request body must be a JSON object.")
    return body


def _claimable(session: dict[str, Any], user_id: str, guest: str) -> bool:
    """A trip can be claimed by `user_id` only if no other account owns it and the guest token matches."""
    owner_user = session.get("user_id") or ""
    if owner_user:
        return owner_user == user_id
    owner_token = session.get("client_token") or ""
    return not owner_token or hmac.compare_digest(owner_token, guest)


def _failures(request: Request, key: str) -> list[float]:
    store = request.app.state.__dict__.setdefault("signin_failures", {})
    now = time.monotonic()
    recent = [t for t in store.get(key, []) if now - t < SIGNIN_WINDOW_SECONDS]
    store[key] = recent
    return recent


def _auth_payload(settings, user: dict[str, Any]) -> dict[str, Any]:
    token = issue_token(settings, user["id"])
    return {"token": token, "token_type": "bearer", "profile_token": token, "user": user_public(user)}


@router.post("/api/account/signup")
async def signup(request: Request) -> dict[str, Any]:
    c = container(request)
    body = await _json_object(request)
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    if "@" not in email or len(password) < 6:
        raise HTTPException(status_code=400, detail="An email and a password of at least 6 characters are needed.")
    try:
        user = await c.store.create_user(email, password_hash(password), str(body.get("display_name", "")).strip())
    except ValueError:
        raise HTTPException(status_code=409, detail="An account with that email already exists.") from None
    session_id = str(body.get("session_id") or "")
    if session_id:
        session = await c.store.get_session(session_id)
        if session and _claimable(session, user["id"], str(body.get("client_token") or "")):
            await c.store.claim_session(session_id, user["id"])
    return _auth_payload(c.settings, user)


@router.post("/api/account/signin")
async def signin(request: Request) -> dict[str, Any]:
    c = container(request)
    body = await _json_object(request)
    email = str(body.get("email", "")).strip().lower()
    password = str(body.get("password", ""))
    if len(_failures(request, email)) >= MAX_SIGNIN_FAILURES:
        raise HTTPException(status_code=429, detail="Too many failed sign-ins. Try again in a few minutes.")
    user = await c.store.get_user_by_email(email)
    if user is None or not password_ok(password, user["password_hash"]):
        _failures(request, email).append(time.monotonic())
        raise HTTPException(status_code=401, detail="That email and password do not match an account.")
    request.app.state.__dict__.setdefault("signin_failures", {}).pop(email, None)
    session_id = str(body.get("session_id") or "")
    if session_id:
        session = await c.store.get_session(session_id)
        if session and _claimable(session, user["id"], str(body.get("client_token") or "")):
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
    body = await _json_object(request)
    session_id = str(body.get("session_id") or "")
    guest = str(body.get("client_token") or "")
    session = await c.store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="That trip no longer exists.")
    if not _claimable(session, user_id, guest):
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
    return await _traveller_view(c, request, identity, client_token, profile)


async def _traveller_view(c, request: Request, identity: str, client_token: str, profile: dict[str, Any]) -> dict[str, Any]:
    """The traveller profile shape shared by GET and PATCH."""
    wishlist = await c.store.list_wishlist(identity)
    sessions = await c.store.list_sessions(200)
    token = presented_token(request, client_token)
    user_id = bearer_user(request)
    mine = [
        row
        for row in sessions
        if (token and (row.get("client_token") or "") == token) or (user_id and row.get("user_id") == user_id)
    ]
    destinations = sorted({row.get("destination", "") for row in wishlist if row.get("destination")})
    return {
        "client_token": client_token or presented_token(request),
        "preferences": profile["preferences"],
        "hard_constraints": profile["hard_constraints"],
        "sessions": len(mine),
        "saved_trips": len(wishlist),
        "destinations": destinations,
        "updated_at": profile.get("updated_at"),
    }


@router.patch("/api/traveller")
async def patch_traveller(request: Request) -> dict[str, Any]:
    c = container(request)
    body = await _json_object(request)
    client_token = str(body.get("client_token") or "")
    identity = identity_for(request, client_token)
    if not identity:
        raise HTTPException(status_code=400, detail="A traveller token is needed.")
    from app.application.memory import merge_preferences

    raw = body.get("preferences") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise HTTPException(status_code=422, detail="preferences must be a list of short phrases.")
    preferences, _added = merge_preferences([], [str(value)[:80] for value in raw])
    profile = await c.store.get_profile(identity) or {"hard_constraints": []}
    saved = await c.store.save_profile(identity, preferences, profile.get("hard_constraints", []))
    return {"profile": await _traveller_view(c, request, identity, client_token, saved)}


@router.post("/api/recall/clear")
async def clear_memory(request: Request) -> dict[str, Any]:
    c = container(request)
    body = await _json_object(request, empty_ok=True)
    identity = identity_for(request, str(body.get("client_token") or ""))
    if not identity:
        return {"cleared": False, "saved_trips": 0, "preferences": 0}
    wishlist = await c.store.list_wishlist(identity)
    profile = await c.store.get_profile(identity) or {}
    preferences = len(profile.get("preferences") or [])
    await c.store.clear_wishlist(identity)
    await c.store.clear_profile(identity)
    return {"cleared": True, "saved_trips": len(wishlist), "preferences": preferences}


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
                "useful": None if row.get("useful") is None else bool(row.get("useful")),
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


@router.delete("/api/wishlist/{saved_id}")
async def delete_wishlist(saved_id: str, request: Request) -> dict[str, Any]:
    c = container(request)
    row = await c.store.get_wishlist(saved_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That saved trip is not there.")
    identity = identity_for(request)
    if row.get("identity") and row["identity"] != identity:
        raise HTTPException(status_code=403, detail="That saved trip belongs to another traveller.")
    deleted = await c.store.delete_wishlist(saved_id)
    return {"deleted": deleted}


@router.post("/api/wishlist/{saved_id}/open")
async def open_wishlist(saved_id: str, request: Request) -> dict[str, Any]:
    c = container(request)
    row = await c.store.get_wishlist(saved_id)
    if row is None:
        raise HTTPException(status_code=404, detail="That saved trip is not there.")
    if row.get("identity") and row["identity"] != identity_for(request):
        raise HTTPException(status_code=403, detail="That saved trip belongs to another traveller.")
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
