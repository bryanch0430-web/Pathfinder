import logging
import time
import uuid
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field
import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import httpx

from app.core.config import settings
from app.services import local_auth

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)


class AuthenticatedUser(BaseModel):
    id: str
    email: Optional[str] = None
    role: Optional[str] = "authenticated"
    user_metadata: Dict[str, Any] = Field(default_factory=dict)


class RequestIdentity(BaseModel):
    """Application-level resolved request identity (either Authenticated User or Guest)."""
    user_id: Optional[str] = None
    guest_id: Optional[str] = None
    user_name: Optional[str] = None

    @property
    def is_authenticated(self) -> bool:
        return self.user_id is not None

    @property
    def is_guest(self) -> bool:
        return self.guest_id is not None

    @property
    def identifier(self) -> Optional[str]:
        return self.user_id or self.guest_id


# In-memory user cache: token -> (AuthenticatedUser, expiry_timestamp)
_TOKEN_CACHE: Dict[str, tuple[AuthenticatedUser, float]] = {}
CACHE_TTL_SECONDS = 300.0  # 5 minutes cache TTL


def _get_cached_user(token: str) -> Optional[AuthenticatedUser]:
    """Retrieve user from in-memory cache if token is present and unexpired."""
    entry = _TOKEN_CACHE.get(token)
    if entry:
        user, expires_at = entry
        if time.time() < expires_at:
            return user
        # Evict expired entry
        _TOKEN_CACHE.pop(token, None)
    return None


def invalidate_cached_token(token: str) -> None:
    """Forget an identity after this server processes a logout request."""
    _TOKEN_CACHE.pop(token, None)


def _cache_user(token: str, user: AuthenticatedUser, exp: Optional[float] = None) -> None:
    """Cache user in memory until token expiry or CACHE_TTL_SECONDS."""
    now = time.time()
    # Prune stale entries if cache grows
    if len(_TOKEN_CACHE) > 500:
        expired_keys = [k for k, (_, exp_time) in _TOKEN_CACHE.items() if now >= exp_time]
        for k in expired_keys:
            _TOKEN_CACHE.pop(k, None)

    ttl = min(exp, now + CACHE_TTL_SECONDS) if exp else now + CACHE_TTL_SECONDS
    _TOKEN_CACHE[token] = (user, ttl)


def _decode_supabase_token(token: str) -> Optional[Dict[str, Any]]:
    """Trust local claims only after verifying the configured project's signature and claims."""
    if not settings.SUPABASE_JWT_SECRET or not settings.SUPABASE_URL:
        return None
    try:
        return jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            audience="authenticated",
            issuer=f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1",
            options={"require": ["exp", "sub", "aud", "iss"]},
        )
    except jwt.PyJWTError as exc:
        logger.debug("Local Supabase JWT verification failed: %s", type(exc).__name__)
        return None


async def _verify_token_with_supabase(token: str) -> Optional[AuthenticatedUser]:
    """Verify token directly against Supabase Auth API endpoint (network fallback)."""
    if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
        return None

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            res = await client.get(
                f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/user",
                headers={
                    "Authorization": f"Bearer {token}",
                    "apikey": settings.SUPABASE_KEY,
                },
            )
            if res.status_code == 200:
                data = res.json()
                if not data.get("id") or data.get("role") != "authenticated":
                    return None
                return AuthenticatedUser(
                    id=data.get("id"),
                    email=data.get("email"),
                    role=data.get("role", "authenticated"),
                    user_metadata=data.get("user_metadata") or {},
                )
    except Exception as e:
        logger.error(f"Error calling Supabase Auth API: {e}")
    return None


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> AuthenticatedUser:
    """
    FastAPI dependency to strictly require an authenticated Supabase user.
    Uses cached verified identities, local HS256 verification when configured,
    and Supabase Auth verification for other signing algorithms.
    """
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials

    # Only identities established by signature verification or Supabase Auth are cached.
    cached_user = _get_cached_user(token)
    if cached_user:
        return cached_user

    # Local accounts: tokens this application signed itself (services/local_auth.py).
    local_claims = local_auth.decode_token(token) if settings.LOCAL_AUTH_ENABLED else None
    if local_claims:
        user = AuthenticatedUser(
            id=local_claims["sub"],
            email=local_claims.get("email"),
            role="authenticated",
            user_metadata=local_claims.get("user_metadata") or {},
        )
        _cache_user(token, user, exp=float(local_claims["exp"]))
        return user

    # Local verification covers legacy HS256 signing keys.
    payload = _decode_supabase_token(token)
    if payload and payload.get("role") == "authenticated":
        try:
            uuid.UUID(payload["sub"])
        except (ValueError, TypeError, AttributeError):
            payload = None
    else:
        payload = None
    if payload:
        user = AuthenticatedUser(
            id=payload["sub"],
            email=payload.get("email"),
            role=payload.get("role", "authenticated"),
            user_metadata=payload.get("user_metadata") or {},
        )
        exp = payload.get("exp")
        _cache_user(token, user, exp=float(exp) if exp else None)
        return user

    # Remote verification also supports projects using asymmetric signing keys.
    user = await _verify_token_with_supabase(token)
    if user:
        # Supabase verified the token; read its expiry only to bound cache lifetime.
        try:
            exp = jwt.decode(
                token, options={"verify_signature": False, "verify_exp": False}
            ).get("exp")
            if exp is not None:
                _cache_user(token, user, exp=float(exp))
        except (jwt.PyJWTError, TypeError, ValueError):
            pass
        return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired authentication token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_request_identity(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_guest_id: Optional[str] = Header(None, alias="X-Guest-ID"),
) -> RequestIdentity:
    """
    Resolve client identity into either an Authenticated User (user_id) or Guest (guest_id).
    Rejects conflicting requests supplying both credentials.
    """
    user_id: Optional[str] = None
    guest_id: Optional[str] = None
    user_name: Optional[str] = None

    # 1. Resolve authenticated user if token is provided
    if credentials and credentials.credentials:
        user = await get_current_user(credentials)
        user_id = user.id
        user_name = (
            user.user_metadata.get("full_name")
            or user.user_metadata.get("name")
            or (user.email.split("@")[0].capitalize() if user.email else None)
        )

    # 2. Resolve guest UUID if header is provided
    if x_guest_id:
        cleaned_guest_id = str(x_guest_id).strip()
        try:
            # Strictly validate that it conforms to standard UUID format
            parsed_uuid = uuid.UUID(cleaned_guest_id)
            guest_id = str(parsed_uuid)
        except (ValueError, AttributeError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid X-Guest-ID format. Must be a valid UUID.",
            )

    # 3. Invariant check: Never allow both identities in the same request
    if user_id and guest_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot supply both authenticated user credentials and X-Guest-ID.",
        )

    return RequestIdentity(user_id=user_id, guest_id=guest_id, user_name=user_name)
