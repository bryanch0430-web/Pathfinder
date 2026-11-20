from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import AuthenticatedUser, get_current_user, invalidate_cached_token, security
from app.core.config import settings
from app.core.database import get_db
from app.repositories.trip import TripRepository
from app.schemas.auth import (
    AuthSessionResponse,
    LocalAccountRequest,
    LocalAccountUser,
    LocalAuthResponse,
    LocalProfileUpdate,
    PrivacyPreviewRequest,
    PrivacySettings,
    ClaimGuestTripsRequest,
    ClaimGuestTripsResponse,
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    ResetPasswordRequest,
    SignUpRequest,
    UserProfileResponse,
)
from app.services import local_auth
from app.services.auth import auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])
trip_repository = TripRepository()


@router.post("/signup", response_model=AuthSessionResponse, status_code=status.HTTP_201_CREATED)
async def signup(request: SignUpRequest):
    """Register a new user account with email and password via Supabase Auth."""
    return await auth_service.sign_up(
        email=request.email,
        password=request.password,
        full_name=request.full_name,
    )


@router.post("/login", response_model=AuthSessionResponse)
async def login(request: LoginRequest):
    """Authenticate with email and password, returning JWT access token and user profile."""
    return await auth_service.login(
        email=request.email,
        password=request.password,
    )


@router.post("/logout", response_model=MessageResponse)
async def logout(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Log out the current authenticated user and invalidate the session."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    result = await auth_service.logout(credentials.credentials)
    invalidate_cached_token(credentials.credentials)
    return result


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(request: ForgotPasswordRequest):
    """Send password reset instructions/OTP to the registered email address."""
    return await auth_service.forgot_password(email=request.email)


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    request: ResetPasswordRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    """Update password using recovery/access token."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Valid recovery token required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return await auth_service.reset_password(
        token=credentials.credentials,
        new_password=request.new_password,
    )


@router.get("/me", response_model=UserProfileResponse)
async def get_current_user_profile(
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Retrieve profile and metadata of the currently authenticated user."""
    return UserProfileResponse(
        id=current_user.id,
        email=current_user.email,
        role=current_user.role or "authenticated",
        full_name=current_user.user_metadata.get("full_name"),
        is_admin=bool(current_user.user_metadata.get("is_admin")),
        user_metadata=current_user.user_metadata,
    )


@router.post("/claim-guest-trips", response_model=ClaimGuestTripsResponse)
async def claim_guest_trips(
    request: ClaimGuestTripsRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Assign trips held by a guest UUID to the authenticated user."""
    count = await trip_repository.claim_guest_trips(db, request.guest_id, current_user.id)
    return ClaimGuestTripsResponse(claimed_count=count)


def _local_session(user) -> LocalAuthResponse:
    token, expires_in = local_auth.issue_token(user)
    return LocalAuthResponse(
        access_token=token,
        expires_in=expires_in,
        user=LocalAccountUser(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            is_admin=user.is_admin,
        ),
    )


@router.post("/local/login", response_model=LocalAuthResponse)
async def local_login(request: LocalAccountRequest):
    """Sign in against the app's own users table, without Supabase."""
    if not settings.LOCAL_AUTH_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local accounts are disabled.")
    user = await local_auth.authenticate(request.email, request.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email or password is incorrect.",
        )
    return _local_session(user)


@router.post("/local/register", response_model=LocalAuthResponse, status_code=status.HTTP_201_CREATED)
async def local_register(request: LocalAccountRequest):
    """Create an ordinary local account (administrator rights are granted out of band)."""
    if not settings.LOCAL_AUTH_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local accounts are disabled.")
    if await local_auth.get_user_by_email(request.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )
    user = await local_auth.create_user(request.email, request.password, request.full_name)
    return _local_session(user)


@router.post("/local/logout", response_model=MessageResponse)
async def local_logout(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Forget a local token in this server's identity cache."""
    if credentials and credentials.credentials:
        invalidate_cached_token(credentials.credentials)
    return MessageResponse(message="Signed out.")


@router.patch("/local/profile", response_model=LocalAuthResponse)
async def local_update_profile(
    request: LocalProfileUpdate,
    current_user: AuthenticatedUser = Depends(get_current_user),
):
    """Rename a local account and hand back a token carrying the new name."""
    if not settings.LOCAL_AUTH_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Local accounts are disabled.")
    if request.new_password:
        stored = await local_auth.get_user(current_user.id)
        if stored is None or not request.current_password or not local_auth.verify_password(request.current_password, stored.password_hash):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The current password does not match.")
    user = await local_auth.update_account(
        current_user.id, full_name=request.full_name, password=request.new_password,
    )
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
    return _local_session(user)


@router.get("/privacy")
async def get_privacy(_user: AuthenticatedUser = Depends(get_current_user)):
    """The privacy switches. Personal details stay masked until one is turned off."""
    from app.services.admin_settings import privacy_view
    return privacy_view()


@router.put("/privacy")
async def put_privacy(
    body: PrivacySettings,
    _user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Save which personal details the planner is allowed to read."""
    from app.services.admin_settings import save_privacy
    try:
        return await save_privacy(db, body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/privacy/preview")
async def preview_privacy(
    body: PrivacyPreviewRequest,
    _user: AuthenticatedUser = Depends(get_current_user),
):
    """Show the masked sentence. The sample is not stored."""
    from app.security.pii import Redaction, check_pattern, preview_redaction
    try:
        chosen = Redaction(
            hkid=body.hkid, hk_phone=body.hk_phone, email=body.email, card=body.card,
            hkid_pattern=check_pattern(body.hkid_pattern),
            hk_phone_pattern=check_pattern(body.hk_phone_pattern),
            email_pattern=check_pattern(body.email_pattern),
            card_pattern=check_pattern(body.card_pattern),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return preview_redaction(body.text, chosen)
