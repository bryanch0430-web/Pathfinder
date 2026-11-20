import uuid
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class SignUpRequest(BaseModel):
    email: str = Field(
        ...,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        description="Valid email address",
    )
    password: str = Field(..., min_length=6, description="Password (at least 6 characters)")
    full_name: Optional[str] = Field(None, description="User full display name")


class LoginRequest(BaseModel):
    email: str = Field(
        ...,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        description="User email address",
    )
    password: str = Field(..., description="User password")


class ForgotPasswordRequest(BaseModel):
    email: str = Field(
        ...,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        description="Registered email to send password reset instructions to",
    )


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=6, description="New password (at least 6 characters)")


class UserProfileResponse(BaseModel):
    id: str
    email: Optional[str] = None
    role: Optional[str] = "authenticated"
    full_name: Optional[str] = None
    is_admin: bool = False
    user_metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None


class LocalAccountRequest(BaseModel):
    """Email and password for the app's own account table (no Supabase)."""
    email: str = Field(
        ...,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        description="Valid email address",
    )
    password: str = Field(..., min_length=6, description="Password (at least 6 characters)")
    full_name: Optional[str] = Field(None, description="User full display name")


class LocalAccountUser(BaseModel):
    id: str
    email: str
    full_name: Optional[str] = None
    is_admin: bool = False


class LocalProfileUpdate(BaseModel):
    full_name: str = Field(..., min_length=1, max_length=120, description="Display name")
    current_password: Optional[str] = None
    new_password: Optional[str] = Field(default=None, min_length=6, max_length=128)


class PrivacySettings(BaseModel):
    hkid: bool = True
    hk_phone: bool = True
    email: bool = True
    card: bool = True
    hkid_pattern: str = ""
    hk_phone_pattern: str = ""
    email_pattern: str = ""
    card_pattern: str = ""


class PrivacyPreviewRequest(PrivacySettings):
    text: str = Field(default="", max_length=2000)


class LocalAuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: LocalAccountUser


class AuthSessionResponse(BaseModel):
    access_token: Optional[str] = None
    token_type: str = "bearer"
    expires_in: Optional[int] = None
    refresh_token: Optional[str] = None
    user: UserProfileResponse
    needs_email_confirmation: bool = False


class MessageResponse(BaseModel):
    message: str
    status: str = "success"


class ClaimGuestTripsRequest(BaseModel):
    guest_id: uuid.UUID


class ClaimGuestTripsResponse(BaseModel):
    claimed_count: int
