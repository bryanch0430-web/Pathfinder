import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import AsyncClient, Response

from app.core.auth import AuthenticatedUser


@pytest.mark.asyncio
async def test_auth_signup_success(client: AsyncClient):
    """Test successful user registration via /api/auth/signup."""
    mock_user_id = str(uuid.uuid4())
    mock_supabase_payload = {
        "access_token": "mock-access-token-123",
        "refresh_token": "mock-refresh-token-456",
        "expires_in": 3600,
        "token_type": "bearer",
        "user": {
            "id": mock_user_id,
            "email": "traveler@example.com",
            "role": "authenticated",
            "user_metadata": {"full_name": "Test Traveler"},
            "created_at": "2026-09-01T12:00:00Z",
        },
    }

    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = Response(200, json=mock_supabase_payload)
    mock_client_instance.__aenter__.return_value = mock_client_instance
    mock_client_instance.__aexit__.return_value = None

    with patch("app.services.auth.httpx.AsyncClient", return_value=mock_client_instance):
        with patch("app.services.auth.settings.SUPABASE_URL", "https://mock.supabase.co"), patch(
            "app.services.auth.settings.SUPABASE_KEY", "mock-key"
        ), patch("app.services.auth.auth_service.base_url", "https://mock.supabase.co"), patch(
            "app.services.auth.auth_service.api_key", "mock-key"
        ):
            res = await client.post(
                "/api/auth/signup",
                json={
                    "email": "traveler@example.com",
                    "password": "secretpassword123",
                    "full_name": "Test Traveler",
                },
            )
            assert res.status_code == 201
            data = res.json()
            assert data["access_token"] == "mock-access-token-123"
            assert data["user"]["email"] == "traveler@example.com"
            assert data["user"]["full_name"] == "Test Traveler"


@pytest.mark.asyncio
async def test_auth_signup_requires_email_confirmation(client: AsyncClient):
    """Supabase may create a user without issuing a session until email confirmation."""
    mock_client = AsyncMock()
    mock_client.post.return_value = Response(200, json={
        "user": {"id": str(uuid.uuid4()), "email": "new@example.com", "user_metadata": {}},
    })
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    with patch("app.services.auth.httpx.AsyncClient", return_value=mock_client), patch(
        "app.services.auth.auth_service.base_url", "https://mock.supabase.co"
    ), patch("app.services.auth.auth_service.api_key", "mock-key"):
        res = await client.post("/api/auth/signup", json={
            "email": "new@example.com", "password": "secretpassword123"
        })
    assert res.status_code == 201
    assert res.json()["access_token"] is None
    assert res.json()["needs_email_confirmation"] is True


@pytest.mark.asyncio
async def test_auth_login_success(client: AsyncClient):
    """Test successful user login via /api/auth/login."""
    mock_user_id = str(uuid.uuid4())
    mock_supabase_payload = {
        "access_token": "mock-login-token-789",
        "refresh_token": "mock-refresh-token-987",
        "expires_in": 3600,
        "token_type": "bearer",
        "user": {
            "id": mock_user_id,
            "email": "traveler@example.com",
            "role": "authenticated",
            "user_metadata": {"full_name": "Test Traveler"},
            "created_at": "2026-09-01T12:00:00Z",
        },
    }

    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = Response(200, json=mock_supabase_payload)
    mock_client_instance.__aenter__.return_value = mock_client_instance
    mock_client_instance.__aexit__.return_value = None

    with patch("app.services.auth.httpx.AsyncClient", return_value=mock_client_instance):
        with patch("app.services.auth.auth_service.base_url", "https://mock.supabase.co"), patch(
            "app.services.auth.auth_service.api_key", "mock-key"
        ):
            res = await client.post(
                "/api/auth/login",
                json={
                    "email": "traveler@example.com",
                    "password": "secretpassword123",
                },
            )
            assert res.status_code == 200
            data = res.json()
            assert data["access_token"] == "mock-login-token-789"
            assert data["user"]["id"] == mock_user_id


@pytest.mark.asyncio
async def test_auth_login_invalid_credentials(client: AsyncClient):
    """Test login failure with invalid credentials."""
    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = Response(
        400, json={"error_description": "Invalid login credentials"}
    )
    mock_client_instance.__aenter__.return_value = mock_client_instance
    mock_client_instance.__aexit__.return_value = None

    with patch("app.services.auth.httpx.AsyncClient", return_value=mock_client_instance):
        with patch("app.services.auth.auth_service.base_url", "https://mock.supabase.co"), patch(
            "app.services.auth.auth_service.api_key", "mock-key"
        ):
            res = await client.post(
                "/api/auth/login",
                json={
                    "email": "wrong@example.com",
                    "password": "wrongpassword",
                },
            )
            assert res.status_code == 401
            data = res.json()
            assert "Invalid login credentials" in data["detail"]


@pytest.mark.asyncio
async def test_auth_login_rejects_missing_session(client: AsyncClient):
    mock_client = AsyncMock()
    mock_client.post.return_value = Response(200, json={
        "user": {"id": str(uuid.uuid4()), "email": "traveler@example.com"}
    })
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    with patch("app.services.auth.httpx.AsyncClient", return_value=mock_client), patch(
        "app.services.auth.auth_service.base_url", "https://mock.supabase.co"
    ), patch("app.services.auth.auth_service.api_key", "mock-key"):
        res = await client.post("/api/auth/login", json={
            "email": "traveler@example.com", "password": "secretpassword123"
        })
    assert res.status_code == 502


@pytest.mark.asyncio
async def test_auth_forgot_password(client: AsyncClient):
    """Test forgot-password recovery trigger."""
    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = Response(200, json={})
    mock_client_instance.__aenter__.return_value = mock_client_instance
    mock_client_instance.__aexit__.return_value = None

    with patch("app.services.auth.httpx.AsyncClient", return_value=mock_client_instance):
        with patch("app.services.auth.auth_service.base_url", "https://mock.supabase.co"), patch(
            "app.services.auth.auth_service.api_key", "mock-key"
        ):
            res = await client.post(
                "/api/auth/forgot-password",
                json={"email": "traveler@example.com"},
            )
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "success"
            assert "traveler@example.com" in data["message"]


@pytest.mark.asyncio
async def test_auth_reset_password(client: AsyncClient):
    """Test password reset using Bearer token."""
    mock_client_instance = AsyncMock()
    mock_client_instance.put.return_value = Response(200, json={})
    mock_client_instance.__aenter__.return_value = mock_client_instance
    mock_client_instance.__aexit__.return_value = None

    with patch("app.services.auth.httpx.AsyncClient", return_value=mock_client_instance):
        with patch("app.services.auth.auth_service.base_url", "https://mock.supabase.co"), patch(
            "app.services.auth.auth_service.api_key", "mock-key"
        ):
            res = await client.post(
                "/api/auth/reset-password",
                headers={"Authorization": "Bearer mock-recovery-token"},
                json={"new_password": "brandnewpassword456"},
            )
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "success"
            assert "successfully" in data["message"].lower()


@pytest.mark.asyncio
async def test_auth_logout(client: AsyncClient):
    """Test user logout."""
    mock_client_instance = AsyncMock()
    mock_client_instance.post.return_value = Response(204)
    mock_client_instance.__aenter__.return_value = mock_client_instance
    mock_client_instance.__aexit__.return_value = None

    with patch("app.services.auth.httpx.AsyncClient", return_value=mock_client_instance):
        with patch("app.services.auth.auth_service.base_url", "https://mock.supabase.co"), patch(
            "app.services.auth.auth_service.api_key", "mock-key"
        ):
            res = await client.post(
                "/api/auth/logout",
                headers={"Authorization": "Bearer mock-active-token"},
            )
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "success"


@pytest.mark.asyncio
async def test_auth_me_endpoint(client: AsyncClient):
    """Test /api/auth/me returns current user profile."""
    mock_user_id = str(uuid.uuid4())
    mock_user = AuthenticatedUser(
        id=mock_user_id,
        email="traveler@example.com",
        role="authenticated",
        user_metadata={"full_name": "Test Traveler"},
    )

    from app.core.auth import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        res = await client.get(
            "/api/auth/me",
            headers={"Authorization": "Bearer valid-token"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["id"] == mock_user_id
        assert data["email"] == "traveler@example.com"
        assert data["full_name"] == "Test Traveler"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_end_to_end_guest_and_user_trip_flow(client: AsyncClient):
    """
    Test full end-to-end flows requested in session:
    1. Guest with generated guest UUID -> create trip -> receive welcome message -> get trip.
    2. User with token -> create trip -> receive welcome message -> get trip -> logout.
    """
    # 1. Guest Flow
    guest_uuid = str(uuid.uuid4())
    guest_headers = {"X-Guest-ID": guest_uuid}

    guest_create = await client.post("/api/trips", headers=guest_headers)
    assert guest_create.status_code == 201
    guest_trip_data = guest_create.json()
    guest_trip_id = guest_trip_data["trip_id"]
    assert "assistant_message" in guest_trip_data
    assert len(guest_trip_data["assistant_message"]["content"]) > 0

    # Guest retrieves their trip
    guest_get = await client.get(f"/api/trips/{guest_trip_id}", headers=guest_headers)
    assert guest_get.status_code == 200
    assert guest_get.json()["trip"]["guest_id"] == guest_uuid

    # 2. Authenticated User Flow
    user_uuid = str(uuid.uuid4())
    user_headers = {"Authorization": "Bearer user-token-abc"}

    with patch(
        "app.core.auth._verify_token_with_supabase",
        return_value=AuthenticatedUser(
            id=user_uuid,
            email="logged_in@example.com",
            role="authenticated",
            user_metadata={"full_name": "Logged Traveler"},
        ),
    ):
        user_create = await client.post("/api/trips", headers=user_headers)
        assert user_create.status_code == 201
        user_trip_data = user_create.json()
        user_trip_id = user_trip_data["trip_id"]
        assert "assistant_message" in user_trip_data
        assert len(user_trip_data["assistant_message"]["content"]) > 0

        # User retrieves their trip
        user_get = await client.get(f"/api/trips/{user_trip_id}", headers=user_headers)
        assert user_get.status_code == 200
        assert user_get.json()["trip"]["user_id"] == user_uuid

        # User logs out
        mock_client_instance = AsyncMock()
        mock_client_instance.post.return_value = Response(204)
        mock_client_instance.__aenter__.return_value = mock_client_instance
        mock_client_instance.__aexit__.return_value = None

        with patch("app.services.auth.httpx.AsyncClient", return_value=mock_client_instance):
            with patch("app.services.auth.auth_service.base_url", "https://mock.supabase.co"), patch(
                "app.services.auth.auth_service.api_key", "mock-key"
            ):
                logout_res = await client.post("/api/auth/logout", headers=user_headers)
                assert logout_res.status_code == 200
                assert logout_res.json()["status"] == "success"


@pytest.mark.asyncio
async def test_local_jwt_decode_and_cache_avoids_remote_auth():
    """A signed token for this project can be verified locally and cached."""
    import jwt
    import time
    from fastapi.security import HTTPAuthorizationCredentials
    from app.core.auth import get_current_user, _TOKEN_CACHE

    test_user_id = str(uuid.uuid4())
    payload = {
        "sub": test_user_id,
        "aud": "authenticated",
        "iss": "https://mock.supabase.co/auth/v1",
        "email": "fastuser@example.com",
        "role": "authenticated",
        "user_metadata": {"full_name": "Fast User"},
        "exp": int(time.time()) + 3600,
    }
    secret = "a-secure-32-byte-secret-key-12345"
    token = jwt.encode(payload, secret, algorithm="HS256")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


    # Clear cache before test
    _TOKEN_CACHE.pop(token, None)

    with patch("app.core.auth.settings.SUPABASE_JWT_SECRET", secret), patch(
        "app.core.auth.settings.SUPABASE_URL", "https://mock.supabase.co"
    ), patch("app.core.auth._verify_token_with_supabase") as mock_remote:
        # First call: local JWT decode, no remote call
        user1 = await get_current_user(creds)
        assert user1.id == test_user_id
        assert user1.email == "fastuser@example.com"
        mock_remote.assert_not_called()

        # Second call: in-memory cache hit, no remote call
        user2 = await get_current_user(creds)
        assert user2.id == test_user_id
        mock_remote.assert_not_called()


@pytest.mark.asyncio
async def test_forged_token_is_rejected_even_with_guest_header():
    import jwt
    import time
    from fastapi import HTTPException
    from fastapi.security import HTTPAuthorizationCredentials
    from app.core.auth import get_request_identity

    token = jwt.encode({
        "sub": str(uuid.uuid4()), "role": "authenticated", "aud": "authenticated",
        "iss": "https://mock.supabase.co/auth/v1", "exp": int(time.time()) + 3600,
    }, "a-different-secure-32-byte-secret-key", algorithm="HS256")
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with patch("app.core.auth.settings.SUPABASE_JWT_SECRET", "the-real-secure-32-byte-secret-key"), patch(
        "app.core.auth.settings.SUPABASE_URL", "https://mock.supabase.co"
    ), patch("app.core.auth._verify_token_with_supabase", return_value=None):
        with pytest.raises(HTTPException) as exc:
            await get_request_identity(creds, str(uuid.uuid4()))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_claim_guest_trips_transfers_only_matching_unowned_records(client: AsyncClient):
    from sqlalchemy import select
    from app.core.auth import get_current_user
    from app.main import app
    from app.models.trip import Trip
    from conftest import TestingSessionLocal

    guest_id = str(uuid.uuid4())
    other_guest_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    already_owned_id = str(uuid.uuid4())
    async with TestingSessionLocal() as db:
        db.add_all([
            Trip(guest_id=guest_id),
            Trip(guest_id=other_guest_id),
            Trip(guest_id=guest_id, user_id=already_owned_id),
        ])
        await db.commit()

    app.dependency_overrides[get_current_user] = lambda: AuthenticatedUser(id=user_id)
    try:
        res = await client.post("/api/auth/claim-guest-trips", json={"guest_id": guest_id})
        assert res.status_code == 200
        assert res.json() == {"claimed_count": 1}
        repeat = await client.post("/api/auth/claim-guest-trips", json={"guest_id": guest_id})
        assert repeat.json() == {"claimed_count": 0}
        async with TestingSessionLocal() as db:
            trips = (await db.execute(select(Trip))).scalars().all()
            assert sum(t.user_id == user_id and t.guest_id is None for t in trips) == 1
            assert sum(t.guest_id == other_guest_id for t in trips) == 1
            assert sum(t.user_id == already_owned_id for t in trips) == 1
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_claim_guest_trips_requires_verified_user(client: AsyncClient):
    with patch("app.core.auth._verify_token_with_supabase", return_value=None):
        missing = await client.post(
            "/api/auth/claim-guest-trips", json={"guest_id": str(uuid.uuid4())}
        )
        invalid = await client.post(
            "/api/auth/claim-guest-trips",
            headers={"Authorization": "Bearer invalid-token"},
            json={"guest_id": str(uuid.uuid4())},
        )
    assert missing.status_code == 401
    assert invalid.status_code == 401

