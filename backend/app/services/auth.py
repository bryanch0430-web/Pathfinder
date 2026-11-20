import logging
from typing import Any, Dict, Optional
import httpx
from fastapi import HTTPException, status

from app.core.config import settings
from app.schemas.auth import (
    AuthSessionResponse,
    MessageResponse,
    UserProfileResponse,
)

logger = logging.getLogger(__name__)


class AuthService:
    """Service orchestrating authentication operations with Supabase GoTrue Auth API."""

    def __init__(self):
        self.base_url = settings.SUPABASE_URL.rstrip("/") if settings.SUPABASE_URL else ""
        self.api_key = settings.SUPABASE_KEY

    def _get_headers(self, token: Optional[str] = None) -> Dict[str, str]:
        headers = {
            "apikey": self.api_key or "",
            "Content-Type": "application/json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _ensure_configured(self):
        if not self.base_url or not self.api_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Supabase Auth is not configured on the backend server.",
            )

    async def sign_up(
        self, email: str, password: str, full_name: Optional[str] = None
    ) -> AuthSessionResponse:
        """Register a new user account with email and password."""
        self._ensure_configured()
        url = f"{self.base_url}/auth/v1/signup"
        payload: Dict[str, Any] = {
            "email": email,
            "password": password,
        }
        if full_name:
            payload["data"] = {"full_name": full_name}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload, headers=self._get_headers())
                data = res.json()

                if res.status_code not in (200, 201):
                    error_msg = data.get("msg") or data.get("error_description") or data.get("message") or "Signup failed"
                    raise HTTPException(
                        status_code=res.status_code if res.status_code in (400, 422, 429) else status.HTTP_400_BAD_REQUEST,
                        detail=error_msg,
                    )

                user_data = data.get("user") or data
                if not user_data.get("id"):
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="Authentication service returned an incomplete signup response.",
                    )
                access_token = data.get("access_token")
                refresh_token = data.get("refresh_token")
                expires_in = data.get("expires_in")
                user_metadata = user_data.get("user_metadata") or {}

                user_profile = UserProfileResponse(
                    id=user_data.get("id"),
                    email=user_data.get("email"),
                    role=user_data.get("role", "authenticated"),
                    full_name=user_metadata.get("full_name") or full_name,
                    user_metadata=user_metadata,
                    created_at=user_data.get("created_at"),
                )

                return AuthSessionResponse(
                    access_token=access_token,
                    refresh_token=refresh_token,
                    expires_in=expires_in,
                    user=user_profile,
                    needs_email_confirmation=not bool(access_token),
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error during Supabase sign_up: {e}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Authentication service error: {str(e)}",
            )

    async def login(self, email: str, password: str) -> AuthSessionResponse:
        """Authenticate user with email and password, returning JWT access and refresh tokens."""
        self._ensure_configured()
        url = f"{self.base_url}/auth/v1/token?grant_type=password"
        payload = {
            "email": email,
            "password": password,
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload, headers=self._get_headers())
                data = res.json()

                if res.status_code != 200:
                    error_msg = data.get("error_description") or data.get("msg") or data.get("message") or "Invalid email or password"
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail=error_msg,
                    )

                user_data = data.get("user") or {}
                if not user_data.get("id") or not data.get("access_token"):
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail="Authentication service returned an incomplete login response.",
                    )
                user_metadata = user_data.get("user_metadata") or {}

                user_profile = UserProfileResponse(
                    id=user_data.get("id"),
                    email=user_data.get("email"),
                    role=user_data.get("role", "authenticated"),
                    full_name=user_metadata.get("full_name"),
                    user_metadata=user_metadata,
                    created_at=user_data.get("created_at"),
                )

                return AuthSessionResponse(
                    access_token=data["access_token"],
                    refresh_token=data.get("refresh_token"),
                    expires_in=data.get("expires_in"),
                    user=user_profile,
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error during Supabase login: {e}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Authentication service error: {str(e)}",
            )

    async def logout(self, token: str) -> MessageResponse:
        """Sign out the current user and invalidate the session."""
        self._ensure_configured()
        url = f"{self.base_url}/auth/v1/logout"

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.post(url, headers=self._get_headers(token))
                # Supabase returns 204 or 200 on successful logout
                if res.status_code in (200, 204):
                    return MessageResponse(message="Successfully logged out.")
                
                # If token was already invalid or expired, return clean success anyway for client idempotence
                return MessageResponse(message="Session ended.")
        except Exception as e:
            logger.warning(f"Warning during Supabase logout call: {e}")
            return MessageResponse(message="Session ended.")

    async def forgot_password(self, email: str) -> MessageResponse:
        """Trigger password recovery email / OTP via Supabase Auth."""
        self._ensure_configured()
        url = f"{self.base_url}/auth/v1/recover"
        payload = {"email": email}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(url, json=payload, headers=self._get_headers())
                if res.status_code not in (200, 201, 204):
                    data = res.json()
                    error_msg = data.get("msg") or data.get("message") or "Failed to initiate password reset"
                    raise HTTPException(
                        status_code=res.status_code if res.status_code in (400, 429) else status.HTTP_400_BAD_REQUEST,
                        detail=error_msg,
                    )

                return MessageResponse(
                    message=f"Password reset instructions sent to {email} if an account exists."
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error during forgot_password: {e}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Authentication service error: {str(e)}",
            )

    async def reset_password(self, token: str, new_password: str) -> MessageResponse:
        """Update password for the authenticated user holding the recovery token."""
        self._ensure_configured()
        url = f"{self.base_url}/auth/v1/user"
        payload = {"password": new_password}

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.put(url, json=payload, headers=self._get_headers(token))
                if res.status_code not in (200, 204):
                    data = res.json()
                    error_msg = data.get("msg") or data.get("message") or "Failed to reset password"
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=error_msg,
                    )

                return MessageResponse(message="Password successfully updated.")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error during reset_password: {e}")
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Authentication service error: {str(e)}",
            )


auth_service = AuthService()
