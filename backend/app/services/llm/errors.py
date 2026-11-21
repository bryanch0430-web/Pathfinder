"""One honest sentence when the language model cannot answer.

Without this, a rejected API key looks like an assistant that keeps asking the
same question: every node falls back to a friendly canned line, so the traveler
never learns that nothing is reaching the model.
"""
from __future__ import annotations

_AUTH_MARKERS = ("401", "403", "unauthorized", "forbidden", "authentication", "api key", "api_key")

AUTH_MESSAGE = (
    "I can't think right now: the language model rejected this app's API key (401 Unauthorized). "
    "Put a valid model key in backend/.env, restart the backend, and send that again."
)

OFFLINE_MESSAGE = "I couldn't reach the language model just now. Give it a moment, then send that again."


def is_auth_failure(exc: BaseException) -> bool:
    """True when the provider refused our credentials rather than just failing."""
    text = f"{type(exc).__name__} {exc}".lower()
    return any(marker in text for marker in _AUTH_MARKERS)


def _response_text(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return ""
    try:
        return str(response.text or "")[:300]
    except Exception:
        return ""


def model_unavailable_message(exc: BaseException) -> str:
    other = getattr(exc, "response", None)
    status = getattr(other, "status_code", None)
    blob = f"{exc} {_response_text(exc)}".lower()
    if "incorrect api key" in blob or "invalid api key" in blob or "api key" in blob and status in (400, 401, 403):
        return (
            "That writer refused its key. For Grok, put a current key from console.x.ai in backend/.env as XAI_API_KEY, "
            "restart the backend, and send that again. You can also paste a key under Your model key."
        )
    if status in (401, 403) or is_auth_failure(exc):
        return AUTH_MESSAGE
    return OFFLINE_MESSAGE
