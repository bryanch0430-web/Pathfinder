"""Limits of one turn that grow with the number of cities the turn works on.

A one-city turn gets exactly the base settings; each further city adds its own allowance, so a long trip is
neither cut short by the single-city deadline nor left without any cap.
"""

from __future__ import annotations

from app.config import Settings

#: The longest a turn may be given, however many cities it covers (unless the base setting is already longer).
DEADLINE_CAP_SECONDS = 900.0


def turn_deadline_for(settings: Settings, segments: int) -> float:
    """Seconds a turn on ``segments`` cities may run: the base deadline plus an allowance per extra city, capped."""
    base = settings.turn_deadline_seconds
    total = base + settings.segment_deadline_seconds * max(0, segments - 1)
    return min(total, max(DEADLINE_CAP_SECONDS, base))


def tool_cap_for(settings: Settings, segments: int) -> int:
    """Tool calls one path may make on ``segments`` cities; 0 (the cap is off) stays 0."""
    base = settings.max_tool_calls_per_turn
    if base == 0:
        return 0
    return base + settings.segment_tool_calls * max(0, segments - 1)
