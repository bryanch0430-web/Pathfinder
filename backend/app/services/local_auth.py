"""Local accounts: password hashing, token issuing and account lookup.

Passwords are stored as scrypt hashes in the app database. Tokens are ordinary
HS256 JWTs with the same claim shape the Supabase path produces (sub, email,
role, aud, iss, user_metadata), so `get_current_user` and every route that reads
`user_metadata` work unchanged.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session_maker
from app.models.user import User

ISSUER = "pathfinder-local"
AUDIENCE = "authenticated"
_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "dklen": 32}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), **_SCRYPT)
    return hmac.compare_digest(digest.hex(), digest_hex)


def issue_token(user: User) -> tuple[str, int]:
    """Return (token, expires_in_seconds) for a stored account."""
    lifetime = timedelta(hours=settings.LOCAL_AUTH_TOKEN_HOURS)
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "email": user.email,
        "role": "authenticated",
        "aud": AUDIENCE,
        "iss": ISSUER,
        "iat": now,
        "exp": now + lifetime,
        "user_metadata": {"full_name": user.full_name, "is_admin": user.is_admin},
    }
    token = jwt.encode(payload, settings.LOCAL_AUTH_SECRET, algorithm="HS256")
    return token, int(lifetime.total_seconds())


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(
            token,
            settings.LOCAL_AUTH_SECRET,
            algorithms=["HS256"],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError:
        return None


async def get_user_by_email(email: str) -> Optional[User]:
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.email == email.strip().lower()))
        return result.scalar_one_or_none()


async def get_user(user_id: str) -> Optional[User]:
    async with async_session_maker() as session:
        return await session.get(User, user_id)


async def authenticate(email: str, password: str) -> Optional[User]:
    user = await get_user_by_email(email)
    if user and verify_password(password, user.password_hash):
        return user
    return None


async def create_user(email: str, password: str, full_name: Optional[str] = None, is_admin: bool = False) -> User:
    async with async_session_maker() as session:
        user = User(
            email=email.strip().lower(),
            password_hash=hash_password(password),
            full_name=(full_name or "").strip() or None,
            is_admin=is_admin,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def set_password(user: User, password: str) -> None:
    async with async_session_maker() as session:
        stored = await session.get(User, user.id)
        if stored is None:
            return
        stored.password_hash = hash_password(password)
        await session.commit()


async def update_account(
    user_id: str,
    *,
    password: Optional[str] = None,
    full_name: Optional[str] = None,
    is_admin: Optional[bool] = None,
) -> Optional[User]:
    """Change an existing account in place; unset arguments are left alone."""
    async with async_session_maker() as session:
        stored = await session.get(User, user_id)
        if stored is None:
            return None
        if password:
            stored.password_hash = hash_password(password)
        if full_name is not None:
            stored.full_name = full_name.strip() or None
        if is_admin is not None:
            stored.is_admin = is_admin
        await session.commit()
        await session.refresh(stored)
        return stored
