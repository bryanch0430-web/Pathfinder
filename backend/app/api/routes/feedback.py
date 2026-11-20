"""Public feedback delivery. The recipient and email credentials stay server-side."""
import logging
from collections import deque
from time import monotonic

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings

router = APIRouter(prefix="/api/feedback", tags=["feedback"])
logger = logging.getLogger(__name__)
_attempts: dict[str, deque[float]] = {}
_global_attempts: deque[float] = deque()


class FeedbackInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    rating: int = Field(ge=1, le=5, strict=True)
    message: str = Field(default="", max_length=2000)
    page: str = Field(default="/", max_length=200, pattern=r"^/[^\r\n]*$")
    website: str = Field(default="", max_length=200)


def check_rate_limit(address: str) -> None:
    """Bounded, per-process limits suitable for the single-instance free backend."""
    now = monotonic()
    for key in list(_attempts):
        if not _attempts[key] or _attempts[key][-1] <= now - 600:
            del _attempts[key]
    while _global_attempts and _global_attempts[0] <= now - 3600:
        _global_attempts.popleft()
    recent = _attempts.setdefault(address, deque())
    while recent and recent[0] <= now - 600:
        recent.popleft()
    if len(recent) >= 3 or len(_global_attempts) >= 20:
        raise HTTPException(429, "Please try again later.", headers={"Retry-After": "600"})
    recent.append(now)
    _global_attempts.append(now)


@router.post("")
async def submit_feedback(data: FeedbackInput, request: Request):
    if data.website:  # Honeypot: silently discard automated form submissions.
        return {"status": "sent"}
    if not settings.RESEND_API_KEY:
        raise HTTPException(503, "Feedback delivery is temporarily unavailable.")
    check_rate_limit(request.client.host if request.client else "unknown")
    payload = {
        "from": settings.FEEDBACK_FROM_EMAIL,
        "to": [settings.FEEDBACK_TO_EMAIL],
        "subject": f"Pathfinder feedback — {data.rating}/5",
        "text": f"Experience rating: {data.rating}/5\nPage: {data.page}\n\n{data.message or '(Rating only — no written feedback.)'}",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post("https://api.resend.com/emails", json=payload,
                                         headers={"Authorization": f"Bearer {settings.RESEND_API_KEY}"})
        if not response.is_success:
            logger.warning("Feedback email provider returned status %s", response.status_code)
            raise HTTPException(502, "Feedback could not be sent. Please try again later.")
    except httpx.HTTPError:
        logger.warning("Feedback email provider request failed")
        raise HTTPException(502, "Feedback could not be sent. Please try again later.") from None
    return {"status": "sent"}
