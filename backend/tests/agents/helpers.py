"""A small, fully valid two-day TripPlan for focus and manual-edit unit tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, time

from backend.schemas.common import GeoPoint, Money, ToolName
from backend.schemas.trip import TripContext
from backend.schemas.trip_plan import (
    DayPlan,
    Hotel,
    HotelStay,
    ItineraryItem,
    Place,
    SourceRef,
    TicketOption,
    TripPlan,
)

DAY1, DAY2 = date(2026, 4, 10), date(2026, 4, 11)
FETCHED_AT = datetime(2026, 4, 1, tzinfo=UTC)


def source(tool: ToolName, call_id: str) -> SourceRef:
    return SourceRef(tool=tool, call_id=call_id, provider="mock", fetched_at=FETCHED_AT)


def place(place_id: str, category: str, *, price: float = 0.0, rating: float = 4.0, lat: float = 35.0) -> Place:
    return Place(
        place_id=place_id,
        name=place_id.replace("-", " ").title(),
        category=category,
        location=GeoPoint(lat=lat, lng=135.75),
        rating=rating,
        indoor=True,
        price=Money(amount=price, currency="JPY"),
        source=source(ToolName.PLACES, f"call-{place_id}"),
    )


def item(place_id: str, on: date, start: time, end: time, *, confirmed: bool = False) -> ItineraryItem:
    return ItineraryItem(
        item_id=f"{place_id}@{on.isoformat()}",
        place_id=place_id,
        title=place_id.replace("-", " ").title(),
        start_time=start,
        end_time=end,
        confirmed=confirmed,
    )


def ticket(ticket_id: str, direction: str, depart: datetime, arrive: datetime) -> TicketOption:
    return TicketOption(
        ticket_id=ticket_id,
        direction=direction,  # type: ignore[arg-type]
        mode="train",
        carrier="JR",
        origin="Tokyo" if direction == "outbound" else "Kyoto",
        destination="Kyoto" if direction == "outbound" else "Tokyo",
        depart_at=depart,
        arrive_at=arrive,
        price=Money(amount=14_000, currency="JPY"),
        source=source(ToolName.TICKETS, f"call-{ticket_id}"),
    )


PLACES = [
    place("temple", "temple", price=500, lat=35.00),
    place("shrine", "shrine", lat=35.01),
    place("museum", "museum", price=1500, lat=35.02),
    place("market", "market", lat=35.03),
]


def make_trip_plan() -> TripPlan:
    """Day 1: temple 10:00-12:00, shrine 12:30-14:30 (confirmed). Day 2: museum 09:00-11:00,
    market 11:30-13:30. Hotel `h1`; tickets `out-1` (outbound) and `ret-1` (return)."""
    return TripPlan(
        plan_id="plan-1",
        destination="Kyoto",
        origin="Tokyo",
        start_date=DAY1,
        end_date=DAY2,
        party_size=2,
        budget=Money(amount=300_000, currency="JPY"),
        days=[
            DayPlan(
                date=DAY1,
                items=[
                    item("temple", DAY1, time(10, 0), time(12, 0)),
                    item("shrine", DAY1, time(12, 30), time(14, 30), confirmed=True),
                ],
            ),
            DayPlan(
                date=DAY2,
                items=[
                    item("museum", DAY2, time(9, 0), time(11, 0)),
                    item("market", DAY2, time(11, 30), time(13, 30)),
                ],
            ),
        ],
        places=PLACES,
        hotel=HotelStay(
            hotel=Hotel(
                hotel_id="h1",
                name="Piece Hostel",
                nightly_price=Money(amount=3_800, currency="JPY"),
                source=source(ToolName.PLACES, "call-h1"),
            ),
            check_in=DAY1,
            check_out=DAY2,
        ),
        tickets=[
            ticket("out-1", "outbound", datetime(2026, 4, 10, 7, 30, tzinfo=UTC), datetime(2026, 4, 10, 8, 45, tzinfo=UTC)),
            ticket("ret-1", "return", datetime(2026, 4, 11, 18, 0, tzinfo=UTC), datetime(2026, 4, 11, 20, 15, tzinfo=UTC)),
        ],
    )


def make_trip_context() -> TripContext:
    return TripContext(
        destination="Kyoto",
        origin="Tokyo",
        start_date=DAY1,
        end_date=DAY2,
        party_size=2,
        budget=Money(amount=300_000, currency="JPY"),
    )
