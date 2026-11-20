from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.api.routes import feedback


@pytest.fixture(autouse=True)
def feedback_settings(monkeypatch):
    monkeypatch.setattr(feedback.settings, "RESEND_API_KEY", "test-feedback-key")
    monkeypatch.setattr(feedback.settings, "FEEDBACK_TO_EMAIL", "owner@example.com")
    feedback._attempts.clear()
    feedback._global_attempts.clear()
    yield
    feedback._attempts.clear()
    feedback._global_attempts.clear()


def mail_client(status=200):
    sender = AsyncMock()
    sender.__aenter__.return_value = sender
    sender.post.return_value = httpx.Response(status, json={"id": "test-email-id"})
    return sender


@pytest.mark.asyncio
async def test_feedback_delivers_rating_comment_to_fixed_recipient(client):
    sender = mail_client()
    with patch.object(feedback.httpx, "AsyncClient", return_value=sender):
        response = await client.post("/api/feedback", json={"rating": 5, "message": " Useful! ", "page": "/explore"})
    assert response.status_code == 200
    assert response.json() == {"status": "sent"}
    arguments = sender.post.call_args.kwargs
    assert arguments["json"]["to"] == ["owner@example.com"]
    assert arguments["json"]["subject"] == "Pathfinder feedback — 5/5"
    assert "Useful!" in arguments["json"]["text"]
    assert "Page: /explore" in arguments["json"]["text"]
    assert arguments["headers"]["Authorization"] == "Bearer test-feedback-key"


@pytest.mark.asyncio
async def test_rating_only_feedback_is_supported(client):
    sender = mail_client()
    with patch.object(feedback.httpx, "AsyncClient", return_value=sender):
        response = await client.post("/api/feedback", json={"rating": 3})
    assert response.status_code == 200
    assert "Rating only" in sender.post.call_args.kwargs["json"]["text"]


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [{"rating": 0}, {"rating": 6}, {"rating": True}, {"rating": "5"},
                                   {"rating": 5, "message": "x" * 2001},
                                   {"rating": 5, "to": "attacker@example.com"},
                                   {"rating": 5, "page": "/\nForged header"}])
async def test_invalid_feedback_never_calls_email_provider(client, body):
    with patch.object(feedback.httpx, "AsyncClient") as sender:
        response = await client.post("/api/feedback", json=body)
    assert response.status_code == 422
    sender.assert_not_called()


@pytest.mark.asyncio
async def test_unconfigured_delivery_does_not_claim_success(client, monkeypatch):
    monkeypatch.setattr(feedback.settings, "RESEND_API_KEY", "")
    response = await client.post("/api/feedback", json={"rating": 4})
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_provider_rejection_does_not_claim_success(client):
    with patch.object(feedback.httpx, "AsyncClient", return_value=mail_client(403)):
        response = await client.post("/api/feedback", json={"rating": 4})
    assert response.status_code == 502


@pytest.mark.asyncio
async def test_provider_timeout_does_not_claim_success(client):
    sender = mail_client()
    sender.post.side_effect = httpx.ReadTimeout("test timeout")
    with patch.object(feedback.httpx, "AsyncClient", return_value=sender):
        response = await client.post("/api/feedback", json={"rating": 4})
    assert response.status_code == 502


@pytest.mark.asyncio
async def test_honeypot_discards_bot_submissions(client):
    with patch.object(feedback.httpx, "AsyncClient") as sender:
        response = await client.post("/api/feedback", json={"rating": 5, "website": "spam"})
    assert response.status_code == 200
    sender.assert_not_called()


@pytest.mark.asyncio
async def test_repeated_feedback_is_rate_limited(client):
    sender = mail_client()
    with patch.object(feedback.httpx, "AsyncClient", return_value=sender):
        for _ in range(3):
            assert (await client.post("/api/feedback", json={"rating": 5})).status_code == 200
        response = await client.post("/api/feedback", json={"rating": 5})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "600"
    assert sender.post.call_count == 3


def test_rate_limits_expire_and_old_addresses_are_removed(monkeypatch):
    monkeypatch.setattr(feedback, "monotonic", lambda: 0)
    for _ in range(3):
        feedback.check_rate_limit("traveler")
    monkeypatch.setattr(feedback, "monotonic", lambda: 601)
    feedback.check_rate_limit("new-traveler")
    assert "traveler" not in feedback._attempts


def test_global_limit_bounds_email_volume():
    for i in range(20):
        feedback.check_rate_limit(f"traveler-{i}")
    with pytest.raises(feedback.HTTPException) as error:
        feedback.check_rate_limit("another-traveler")
    assert error.value.status_code == 429
