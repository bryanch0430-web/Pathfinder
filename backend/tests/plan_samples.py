"""Small hand-made plans for the prompt-context tests. No source is called; every value is written here."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.domain.models import (
    CostBreakdown,
    CostLine,
    DateRange,
    DayPlan,
    Destination,
    HotelStay,
    ItemType,
    Money,
    PlanAlternative,
    PlanItem,
    PriceKind,
    SourceRef,
    TicketOption,
    TripPlan,
)

START = date(2026, 12, 3)
_SOURCE = [
    SourceRef(title="Place page", url="https://example.com/place", source="test")
]


def stop(item_id: str, name: str, start: str, end: str, **more) -> PlanItem:
    return PlanItem(
        id=item_id,
        type=ItemType.ATTRACTION,
        name=name,
        start=start,
        end=end,
        source_refs=_SOURCE,
        **more,
    )


def meal(
    item_id: str,
    name: str,
    start: str,
    end: str,
    slot: str = "lunch",
    *,
    alternatives: int = 0,
) -> PlanItem:
    return PlanItem(
        id=item_id,
        type=ItemType.MEAL,
        name=name,
        start=start,
        end=end,
        meal_slot=slot,
        rating=4.4,
        review_count=812,
        price_level="$$",
        cuisine="soba",
        booking_url="https://example.com/book",
        source_refs=_SOURCE,
        alternatives=[
            PlanAlternative(
                id=f"{item_id}_alt{n}",
                name=f"Other table {n} near {name}",
                rating=4.5,
                price_level="$$",
                distance_m=300 + n,
                booking_url="https://example.com/alt",
            )
            for n in range(alternatives)
        ],
    )


def hotel(days: int, hotel_id: str = "hotel_1") -> HotelStay:
    return HotelStay(
        id=hotel_id,
        name="Hotel Granvia",
        check_in=START,
        check_out=START + timedelta(days=days - 1),
        price_per_night=Money(amount=1200, currency="HKD"),
        price_kind=PriceKind.LIVE,
        rating=4.5,
        review_count=2310,
        distance_to_centre_km=0.4,
        booking_url="https://example.com/hotel",
    )


def flight(ticket_id: str, operator: str, *, selected: bool = False) -> TicketOption:
    return TicketOption(
        id=ticket_id,
        mode="flight",
        name=f"{operator} HKG-KIX",
        origin="HKG",
        destination="KIX",
        operator=operator,
        depart_at=datetime(2026, 12, 3, 9, 10),
        arrive_at=datetime(2026, 12, 3, 13, 55),
        return_depart_at=datetime(2026, 12, 4, 15, 0),
        return_arrive_at=datetime(2026, 12, 4, 18, 20),
        price=Money(amount=2300, currency="HKD"),
        price_kind=PriceKind.LIVE,
        booking_url="https://example.com/flight",
        selected=selected,
    )


def make_plan(
    days: int = 2,
    *,
    alternatives: int = 0,
    stops_per_day: int = 1,
    name_pad: str = "",
    with_stay: bool = True,
    with_tickets: bool = True,
    with_cost: bool = True,
) -> TripPlan:
    day_plans = []
    for n in range(1, days + 1):
        items = [
            stop(
                f"item_d{n}_s{k}",
                f"Temple {n}.{k}{name_pad}",
                f"{8 + k:02d}:00",
                f"{8 + k:02d}:50",
                rating=4.7,
                review_count=5100,
            )
            for k in range(1, stops_per_day + 1)
        ]
        items.append(
            meal(
                f"item_d{n}_lunch",
                f"Soba house {n}{name_pad}",
                "13:00",
                "14:00",
                alternatives=alternatives,
            )
        )
        items.append(
            meal(
                f"item_d{n}_dinner",
                f"Izakaya {n}{name_pad}",
                "19:00",
                "20:00",
                "dinner",
                alternatives=alternatives,
            )
        )
        day_plans.append(
            DayPlan(
                date=START + timedelta(days=n - 1), title=f"Day {n} walk", items=items
            )
        )
    return TripPlan(
        session_id="s1",
        version=3,
        destination=Destination(name="Kyoto"),
        dates=DateRange(start=START, end=START + timedelta(days=days - 1)),
        party_size=2,
        budget=Money(amount=12000, currency="HKD"),
        days=day_plans,
        hotels=[hotel(days)] if with_stay else [],
        tickets=[
            flight("ticket_1", "Cathay", selected=True),
            flight("ticket_2", "HK Express"),
        ]
        if with_tickets
        else [],
        cost=CostBreakdown(
            currency="HKD",
            total=8200,
            budget=12000,
            status="within",
            lines=[
                CostLine(label="hotel", amount=2400),
                CostLine(label="flights", amount=4600),
            ],
        )
        if with_cost
        else CostBreakdown(),
    )


def make_two_city_plan() -> TripPlan:
    """Kyoto 3-5 December then Osaka 5-7 December 2026: five days, a stay in each city."""
    from app.domain.models import TripSegment

    kyoto = DateRange(start=START, end=START + timedelta(days=2))
    osaka = DateRange(start=START + timedelta(days=2), end=START + timedelta(days=4))
    days = [
        DayPlan(
            date=START + timedelta(days=n),
            title=f"Day {n + 1}",
            items=[stop(f"item_d{n + 1}_s1", f"Sight {n + 1}", "09:00", "10:00")],
        )
        for n in range(5)
    ]
    stay_a = hotel(3, "hotel_a")
    stay_b = hotel(3, "hotel_b").model_copy(update={"name": "Osaka Inn", "check_in": osaka.start, "check_out": osaka.end})
    return TripPlan(
        session_id="s2",
        destination=Destination(name="Kyoto"),
        origin="Hong Kong",
        dates=DateRange(start=kyoto.start, end=osaka.end),
        party_size=2,
        days=days,
        hotels=[stay_a, stay_b],
        segments=[
            TripSegment(id="seg_a", destination=Destination(name="Kyoto"), dates=kyoto),
            TripSegment(id="seg_b", destination=Destination(name="Osaka"), dates=osaka),
        ],
    )
