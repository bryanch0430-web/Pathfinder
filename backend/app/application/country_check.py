"""Ask which cities when a trip is planned for a whole country or region.

"Japan, 14 days" has no itinerary that a single city search can give. The geocoder says what kind of place the
destination is (never guessed from the name); for a country or a first-level region and a trip of four days or more,
the traveller is asked which cities to visit, in what order, before any agent runs.
"""

from __future__ import annotations

from typing import Any

from app.application.budget_check import PrePlanQuestion
from app.domain.models import GeoLocation, TripContext

#: A trip shorter than this plans as it always did, whatever the place is.
COUNTRY_QUESTION_MIN_DAYS = 4
WHOLE_PLACE_REASON = "whole_place"


def country_question(
    place: GeoLocation | None, name: str, *, days: int | None
) -> PrePlanQuestion | None:
    """The question for a country or region of ``days`` days or more, else None."""
    if place is None or place.kind not in {"country", "region"}:
        return None
    if days is None or days < COUNTRY_QUESTION_MIN_DAYS:
        return None
    return PrePlanQuestion(
        question=(
            f"{name} is a whole country/region. Which cities do you want to visit, and in what order? "
            "For example: Tokyo 4 nights, then Kyoto 3."
        ),
        slots=["destination"],
        reason_code=WHOLE_PLACE_REASON,
    )


async def whole_place_question(
    registry: Any, context: TripContext
) -> PrePlanQuestion | None:
    """The question for a one-stop trip to a whole country or region; None for several cities, a city, a short trip,
    or a place the geocoder cannot tell the kind of (or cannot find: planning reports that itself)."""
    if len(context.route) > 1 or not context.destination.strip():
        return None
    days = context.days
    if days is None and context.start_date and context.end_date:
        days = (context.end_date - context.start_date).days + 1
    if days is None or days < COUNTRY_QUESTION_MIN_DAYS:
        return None
    try:
        place = await registry.geocoder.geocode(context.destination)
    except Exception:  # noqa: BLE001 - planning looks the place up again and reports a failure itself
        return None
    return country_question(place, context.destination.strip(), days=days)
