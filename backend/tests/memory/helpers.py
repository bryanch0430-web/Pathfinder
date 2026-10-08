"""Shared builders for memory/db tests: a minimal but fully valid `TripPlan`."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta

from backend.schemas.common import Money, ToolName
from backend.schemas.trip import ConstraintKind, HardConstraint, TripContext
from backend.schemas.trip_plan import (
    DayPlan,
    Hotel,
    HotelStay,
    ItineraryItem,
    Place,
    SourceRef,
    TripPlan,
)
from backend.settings import Settings

FETCHED_AT = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

# (name, category) per destination; unknown destinations get generic sights.
DEFAULT_PLACES: dict[str, list[tuple[str, str]]] = {
    "kyoto": [
        ("Fushimi Inari Taisha", "shrine"),
        ("Kiyomizu-dera", "temple"),
        ("Arashiyama Bamboo Grove", "garden"),
    ],
    "hong kong": [
        ("Victoria Peak", "viewpoint"),
        ("Tsim Sha Tsui Promenade", "waterfront"),
        ("Temple Street Night Market", "market"),
    ],
    "paris": [
        ("Louvre Museum", "museum"),
        ("Eiffel Tower", "landmark"),
        ("Jardin du Luxembourg", "park"),
    ],
}


def source(call_id: str = "call-1", tool: ToolName = ToolName.PLACES) -> SourceRef:
    return SourceRef(tool=tool, call_id=call_id, provider="mock", fetched_at=FETCHED_AT)


def make_plan(
    destination: str = "Kyoto",
    *,
    days: int = 3,
    party_size: int = 2,
    budget: Money | None = Money(amount=800, currency="USD"),
    hotel_style: str | None = "ryokan",
    places: Sequence[tuple[str, str]] | None = None,
    start: date = date(2026, 11, 10),
    plan_id: str = "plan-1",
) -> TripPlan:
    """A small valid plan: one itinerary item per day (cycling through the places), a hotel
    stay and `places` as (name, category) pairs. Satisfies every TripPlan validator."""
    chosen = list(places) if places is not None else DEFAULT_PLACES.get(
        destination.lower(), [("Old Town", "sights"), ("City Museum", "museum")]
    )
    place_models = [
        Place(
            place_id=f"p{i}",
            name=name,
            category=category,
            source=source(f"place-{i}"),
        )
        for i, (name, category) in enumerate(chosen)
    ]
    end = start + timedelta(days=days - 1)
    day_plans = [
        DayPlan(
            date=start + timedelta(days=offset),
            items=[
                ItineraryItem(
                    item_id=f"i{offset}",
                    place_id=place_models[offset % len(place_models)].place_id,
                    title=place_models[offset % len(place_models)].name,
                )
            ],
        )
        for offset in range(days)
    ]
    hotel = None
    if hotel_style is not None:
        hotel = HotelStay(
            hotel=Hotel(
                hotel_id="h1",
                name=f"{destination} Stay",
                nightly_price=Money(amount=150, currency="USD"),
                style=hotel_style,
                source=source("hotel-1", ToolName.PLACES),
            ),
            check_in=start,
            check_out=end + timedelta(days=1),
        )
    return TripPlan(
        plan_id=plan_id,
        destination=destination,
        start_date=start,
        end_date=end,
        party_size=party_size,
        budget=budget,
        days=day_plans,
        places=place_models,
        hotel=hotel,
    )


def default_settings(**overrides: object) -> Settings:
    """Settings that ignore any local .env so tests are hermetic."""
    return Settings(_env_file=None, **overrides)  # type: ignore[arg-type]


def make_context(
    destination: str | None = "Kyoto",
    *,
    days: int | None = 3,
    party_size: int | None = 2,
    budget: Money | None = Money(amount=800, currency="USD"),
    hotel_style: str | None = None,
    must_visit: Sequence[str] = (),
    avoid: Sequence[str] = (),
) -> TripContext:
    constraints = [HardConstraint(kind=ConstraintKind.MUST_VISIT, value=v) for v in must_visit]
    constraints += [HardConstraint(kind=ConstraintKind.AVOID, value=v) for v in avoid]
    return TripContext(
        destination=destination,
        days=days,
        party_size=party_size,
        budget=budget,
        hotel_style=hotel_style,
        hard_constraints=constraints,
    )
