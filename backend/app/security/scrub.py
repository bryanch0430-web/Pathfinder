"""Remove secrets from text before it is stored, logged or shown."""

from __future__ import annotations

from typing import Iterable

MIN_SECRET_LENGTH = 6


def scrub(text: str, secrets: Iterable[str]) -> str:
    """Replace every secret of 6 or more characters in ``text`` with ``[redacted]``."""
    # Longest first, so a secret that contains another is removed whole.
    for secret in sorted({s for s in secrets if s and len(s) >= MIN_SECRET_LENGTH}, key=len, reverse=True):
        text = text.replace(secret, "[redacted]")
    return text
