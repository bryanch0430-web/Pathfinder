"""Whether a traveller session is still live for the contact desk.

A session stays live while its page is open, or for 24 hours after the last
change. After that, with the page closed, it is a completed record.
"""

from __future__ import annotations

from datetime import datetime, timedelta

PAGE_OPEN = timedelta(seconds=25)
LIVE_FOR = timedelta(hours=24)


def session_state(updated_at: datetime, seen_at: datetime | None, now: datetime) -> tuple[str, bool]:
    """``live`` or ``completed``, and whether the page is open right now."""
    on_page = seen_at is not None and timedelta(0) <= now - seen_at <= PAGE_OPEN
    live = on_page or timedelta(0) <= now - updated_at <= LIVE_FOR
    return ("live" if live else "completed"), on_page
