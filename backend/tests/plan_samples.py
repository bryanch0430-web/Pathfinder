"""Small hand-made plans for the prompt-context tests. No source is called; every value is written here."""

from __future__ import annotations

import itertools
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
    from app.domain.models import TransportLeg, TripSegment

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
        legs=[
            TransportLeg(id="leg_out", from_place="Hong Kong", to_place="Kyoto", depart_date=kyoto.start, kind="outbound"),
            TransportLeg(id="leg_ab", from_place="Kyoto", to_place="Osaka", depart_date=osaka.start, kind="between"),
            TransportLeg(id="leg_home", from_place="Osaka", to_place="Hong Kong", depart_date=osaka.end, kind="return"),
        ],
        tickets=[
            _one_way("t_out_1", "flight", "leg_out", "Cathay HKG-KIX", 9, selected=True),
            _one_way("t_out_2", "flight", "leg_out", "HK Express HKG-KIX", 11),
            _one_way("t_ab_train", "train", "leg_ab", "Kyoto to Osaka by train", 9, estimated=True, selected=True),
            _one_way("t_ab_bus", "bus", "leg_ab", "Kyoto to Osaka by bus", 10, estimated=True),
            _one_way("t_home_1", "flight", "leg_home", "Cathay KIX-HKG", 16),
        ],
    )


def _one_way(ticket_id: str, mode: str, leg_id: str, name: str, hour: int, **more) -> TicketOption:
    return TicketOption(
        id=ticket_id,
        mode=mode,
        leg_id=leg_id,
        name=name,
        depart_at=datetime(2026, 12, 3, hour, 0),
        arrive_at=datetime(2026, 12, 3, hour + 2, 0),
        **more,
    )


def make_route_plan(cities: list[str], nights: int = 2) -> TripPlan:
    """A plan that visits ``cities`` in order, ``nights`` nights each from 3 December 2026: one stop a day, a stay in each
    city (``hotel_1``, ``hotel_2``...) and a train between consecutive cities. A city named twice is two segments."""
    from app.domain.models import TransportLeg, TripSegment

    segments = [
        TripSegment(
            id=f"seg_{n}",
            destination=Destination(name=city),
            dates=DateRange(
                start=START + timedelta(days=(n - 1) * nights), end=START + timedelta(days=n * nights)
            ),
        )
        for n, city in enumerate(cities, 1)
    ]
    total = len(cities) * nights
    days = [
        DayPlan(
            date=START + timedelta(days=d),
            title=f"Day {d + 1}",
            items=[stop(f"item_d{d + 1}", f"Sight {d + 1}", "09:00", "10:00")],
        )
        for d in range(total + 1)
    ]
    stays = [
        hotel(3, f"hotel_{n}").model_copy(
            update={"name": f"{segment.destination.name} Inn {n}", "check_in": segment.dates.start, "check_out": segment.dates.end}
        )
        for n, segment in enumerate(segments, 1)
    ]
    legs = [
        TransportLeg(id=f"leg_{n}", from_place=a.destination.name, to_place=b.destination.name, depart_date=b.dates.start, kind="between")
        for n, (a, b) in enumerate(itertools.pairwise(segments), 1)
    ]
    tickets = [
        TicketOption(id=f"train_{leg.id}", mode="train", leg_id=leg.id, name=f"{leg.from_place} to {leg.to_place}", estimated=True, selected=True)
        for leg in legs
    ]
    return TripPlan(
        session_id="s3",
        destination=segments[0].destination,
        dates=DateRange(start=START, end=START + timedelta(days=total)),
        party_size=2,
        days=days,
        hotels=stays,
        segments=segments,
        legs=legs,
        tickets=tickets,
    )
