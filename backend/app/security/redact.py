"""Keep API keys out of anything that reaches a browser, a log line or the database."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

# A Google Places photo resource name, exactly as the Places API returns it.
GOOGLE_PHOTO_NAME = re.compile(r"^places/[A-Za-z0-9_-]+/photos/[A-Za-z0-9_-]+$")
MIN_WIDTH, MAX_WIDTH, DEFAULT_WIDTH = 100, 1600, 900

# The old shape: https://places.googleapis.com/v1/places/X/photos/Y/media?maxWidthPx=900&key=SECRET
_OLD_GOOGLE_URL = re.compile(
    r"https?:(?:\\?/){2}places\.googleapis\.com(?:\\?/)v1(?:\\?/)"
    r"(places(?:\\?/)[A-Za-z0-9_-]+(?:\\?/)photos(?:\\?/)[A-Za-z0-9_-]+)(?:\\?/)media"
    r"(?:\?(?:[^\"'\s\\<>]|\\u0026)*)?"
)
_WIDTH = re.compile(r"maxWidthPx=(\d+)")
# key=..., api_key=..., apikey=..., X-Goog-Api-Key=... as a query parameter or header-like pair.
_SECRET_PAIR = re.compile(r"(?i)((?:[?&;]|\\u0026|\b)(?:x-goog-api-key|api[_-]?key|key)=)[^&\s\"'\\<>]+")


def google_photo_url(photo_name: str, width: int = DEFAULT_WIDTH) -> str:
    """The app's own picture route for a Google photo; the key is added server-side."""
    return f"/api/images/google?photo={quote(photo_name, safe='')}&w={clamp_width(width)}"


def clamp_width(width: Any) -> int:
    try:
        value = int(width)
    except (TypeError, ValueError):
        value = DEFAULT_WIDTH
    return max(MIN_WIDTH, min(MAX_WIDTH, value))


def _rewrite_old(match: re.Match[str]) -> str:
    name = match.group(1).replace("\\/", "/")
    width = _WIDTH.search(match.group(0))
    return google_photo_url(name, int(width.group(1)) if width else DEFAULT_WIDTH)


def redact_secrets(text: str) -> str:
    """Mask the value of any key=/api_key= pair, for logs and error text."""
    if not text or "=" not in text:
        return text
    return _SECRET_PAIR.sub(r"\1REDACTED", text)


def scrub_text(text: str) -> str:
    """Rewrite old keyed Google photo URLs to the proxy route, then mask any other key pair.

    Works on raw JSON text too (escaped slashes and \u0026 are handled), so a stored
    plan can be cleaned without parsing it.
    """
    if not text or ("googleapis.com" not in text and "=" not in text):
        return text
    if "places.googleapis.com" in text:
        text = _OLD_GOOGLE_URL.sub(_rewrite_old, text)
    return redact_secrets(text)


def scrub_url(url: str) -> str:
    return scrub_text(url) if isinstance(url, str) else url


def scrub_json_value(value: Any) -> Any:
    """Scrub every string inside a parsed JSON value (in place for lists and dicts)."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, list):
        return [scrub_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: scrub_json_value(item) for key, item in value.items()}
    return value


_log_hook_installed = False


def install_log_redaction() -> None:
    """Mask key=... pairs in every log record, so a logged request URL never carries a key."""
    global _log_hook_installed
    if _log_hook_installed:
        return
    import logging

    previous = logging.getLogRecordFactory()

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = previous(*args, **kwargs)
        try:
            message = record.getMessage()
            if "=" in message:
                masked = redact_secrets(message)
                if masked != message:
                    record.msg, record.args = masked, None
        except Exception:  # noqa: BLE001 - logging must never raise
            pass
        return record

    logging.setLogRecordFactory(factory)
    _log_hook_installed = True
