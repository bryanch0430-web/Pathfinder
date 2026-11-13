"""The page stops the trip form at the limits that the server refuses to go beyond.

The details request is refused with 422 over TRIP_LINES_MAX preferences, TRIP_LINE_MAX_LENGTH characters in a
preference and TRIP_FIELD_MAX_LENGTH characters in the destination or the departure city, and over TRIP_STOPS_MAX cities. A form that let the
traveller type past them could only answer a save with a generic error.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.domain.models import (
    TRIP_FIELD_MAX_LENGTH,
    TRIP_LINE_MAX_LENGTH,
    TRIP_LINES_MAX,
    TRIP_STOPS_MAX,
)

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src"


def test_the_limits_the_page_knows_are_the_servers() -> None:
    text = (FRONTEND / "lib" / "limits.ts").read_text(encoding="utf-8")
    found = {name: int(value) for name, value in re.findall(r"export const (\w+) = (\d+);", text)}

    assert found == {
        "TRIP_LINES_MAX": TRIP_LINES_MAX,
        "TRIP_LINE_MAX_LENGTH": TRIP_LINE_MAX_LENGTH,
        "TRIP_FIELD_MAX_LENGTH": TRIP_FIELD_MAX_LENGTH,
        "TRIP_STOPS_MAX": TRIP_STOPS_MAX,
    }


def test_the_trip_form_stops_at_them() -> None:
    form = (FRONTEND / "design-system" / "molecules.tsx").read_text(encoding="utf-8")

    assert "addTags(preferenceTags, prefInput, TRIP_LINES_MAX, TRIP_LINE_MAX_LENGTH)" in form
    assert form.count("maxLength={TRIP_FIELD_MAX_LENGTH}") == 2  # the destination and the departure city
    assert "limitList(splitList(event.target.value), TRIP_LINES_MAX, TRIP_LINE_MAX_LENGTH)" in form  # the constraints
