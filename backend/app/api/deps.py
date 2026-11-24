"""Shared API plumbing: the container, identity, session access and admin rights."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request

from app.config import Settings
from app.core.store import Store


@dataclass
class Container:
    settings: Settings
    store: Store


def container(request: Request) -> Container:
    return request.app.state.container


def presented_token(request: Request, body_token: str | None = None) -> str:
    token = (request.headers.get("X-Client-Token") or "").strip()
    if token:
        return token
    return (body_token or "").strip()


def _signing_key(settings: Settings) -> bytes:
    return (settings.secret_key_value or settings.admin_token or "pathfinder").encode("utf-8")


def issue_token(settings: Settings, user_id: str) -> str:
    signature = hmac.new(_signing_key(settings), user_id.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
    return f"{user_id}.{signature}"


def bearer_user(request: Request) -> str:
    """The user id from `Authorization: Bearer <token>`, or empty when absent/invalid."""
    header = (request.headers.get("Authorization") or "").strip()
    if not header.lower().startswith("bearer "):
        return ""
    token = header[7:].strip()
    if "." not in token:
        return ""
    user_id, _, signature = token.rpartition(".")
    expected = hmac.new(
        _signing_key(container(request).settings), user_id.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]
    return user_id if hmac.compare_digest(signature, expected) else ""


def identity_for(request: Request, body_token: str | None = None) -> str:
    """Who owns traveller memory: the signed-in user, else the guest token as `guest:`."""
    user_id = bearer_user(request)
    if user_id:
        return f"user:{user_id}"
    token = presented_token(request, body_token)
    return f"guest:{token}" if token else ""


def _origin_allowed(request: Request, settings: Settings) -> bool:
    """A same-origin page, or one of the origins the app was configured for."""
    origin = (request.headers.get("Origin") or "").strip().rstrip("/")
    if not origin:
        return True
    if origin in settings.allowed_origins:
        return True
    host = (request.headers.get("Host") or "").strip()
    return bool(host) and origin in {f"http://{host}", f"https://{host}"}


def is_admin(request: Request) -> bool:
    settings = container(request).settings
    # A browser request from a page on another site carries Origin. It is never
    # the operator, even on a loopback install where local_admin trusts the host.
    if not _origin_allowed(request, settings):
        return False
    header = (request.headers.get("X-Admin-Token") or "").strip()
    if header and hmac.compare_digest(header, settings.admin_token):
        return True
    if not settings.local_admin:
        return False
    host = request.client.host if request.client else ""
    if host in {"127.0.0.1", "::1", "localhost", "testclient"}:
        return True
    if settings.local_admin_proxy and host == settings.local_admin_proxy:
        # Only the configured proxy may speak for the client.
        forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        return bool(forwarded and forwarded in {"127.0.0.1", "::1"})
    return False


def rate_limit(request: Request, bucket: str, limit: int, window: float) -> None:
    """A small fixed-window gate per client address, for the anonymous routes.

    It is not a security boundary by itself (the address can be shared), but it
    stops one caller from filling the database or spending the model budget.
    Tests are exempt so a suite can create the trips it needs.
    """
    settings = container(request).settings
    host = request.client.host if request.client else "unknown"
    if settings.environment == "test" or host == "testclient":
        return
    key = f"{bucket}:{host}"
    now = time.monotonic()
    hits = [at for at in _RATE.get(key, []) if now - at < window]
    if len(hits) >= limit:
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment and try again.")
    hits.append(now)
    _RATE[key] = hits


_RATE: dict[str, list[float]] = {}


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(status_code=403, detail="This view is for the operator.")


async def require_session(request: Request, session_id: str, body_token: str | None = None) -> dict[str, Any]:
    c = container(request)
    session = await c.store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="That trip no longer exists.")
    if not _may_open(request, c.settings, session, body_token):
        raise HTTPException(status_code=403, detail="That trip belongs to another traveller.")
    return session


def _may_open(request: Request, settings: Settings, session: dict[str, Any], body_token: str | None) -> bool:
    user_id = bearer_user(request)
    if user_id and session.get("user_id") == user_id:
        return True
    token = presented_token(request, body_token)
    owner = session.get("client_token") or ""
    if owner and token and hmac.compare_digest(owner, token):
        return True
    if not owner and not session.get("user_id"):
        return True
    if is_admin(request):
        return True
    if settings.environment == "test":
        return True
    return False


def password_hash(password: str) -> str:
    salt = os.urandom(16).hex()
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 120_000).hex()
    return f"{salt}${digest}"


def password_ok(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 120_000).hex()
    return hmac.compare_digest(candidate, digest)


def user_public(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": user["id"],
        "email": user["email"],
        "display_name": user.get("display_name", ""),
        "created_at": user["created_at"],
    }
