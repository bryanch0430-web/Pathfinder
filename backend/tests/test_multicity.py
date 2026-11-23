from __future__ import annotations

from datetime import date, timedelta

from app.domain.models import (
    DateRange,
    DayPlan,
    Destination,
    PlanItem,
    TripContext,
    TripPlan,
    TripStop,
)
from app.plan.continuity import route_continuity_problems
from app.plan.multicity import _merge, segment_ranges


def _context() -> TripContext:
    return TripContext(
        session_id="multi-test",
        destination="Kyoto and Osaka",
        start_date="2027-04-10",
        end_date="2027-04-14",
        days=5,
        stops=[TripStop(city="Kyoto", nights=2), TripStop(city="Osaka", nights=2)],
    )


def _plan(city: str, start: date, end: date) -> TripPlan:
    days = []
    cursor = start
    index = 1
    while cursor <= end:
        days.append(
            DayPlan(
                date=cursor.isoformat(),
                title=city,
                items=[
                    PlanItem(
                        id=f"{city}-{index}",
                        type="attraction",
                        name=f"{city} stop {index}",
                        start="10:00",
                        end="11:00",
                    )
                ],
            )
        )
        cursor += timedelta(days=1)
        index += 1
    return TripPlan(
        trip_id=f"trip-{city}",
        session_id="multi-test",
        destination=Destination(name=city),
        dates=DateRange(start=start.isoformat(), end=end.isoformat()),
        days=days,
    )


def test_segment_ranges_cover_every_day_once():
    context = _context()
    ranges = segment_ranges(context)
    assert [span.city for span in ranges] == ["Kyoto", "Osaka"]
    assert ranges[0].start == date(2027, 4, 10)
    assert ranges[-1].end == date(2027, 4, 14)
    assert sum(span.days for span in ranges) == 5
    assert ranges[1].start == ranges[0].end + timedelta(days=1)


def test_merged_plan_has_ordered_city_segments_and_continuity():
    context = _context()
    ranges = segment_ranges(context)
    plan = _merge(
        [
            _plan("Kyoto", ranges[0].start, ranges[0].end),
            _plan("Osaka", ranges[1].start, ranges[1].end),
        ],
        ranges,
        context,
    )
    assert [day.city for day in plan.days] == ["Kyoto", "Kyoto", "Osaka", "Osaka", "Osaka"]
    assert [segment.destination["name"] for segment in plan.segments] == ["Kyoto", "Osaka"]
    assert not route_continuity_problems(plan, context)


def test_multi_city_builder_joins_segment_plans(monkeypatch):
    import asyncio

    from app.config import Settings
    from app.core.trace import TurnTrace
    from app.evidence.models import Bundle
    from app.plan import multicity
    from app.plan.agents import Gathered

    async def fake_gather(**kwargs):
        return Gathered(Bundle())

    async def fake_compose(*, context, **kwargs):
        start = date.fromisoformat(context.start_date)
        end = date.fromisoformat(context.end_date)
        return _plan(context.destination, start, end), None

    async def emit(*args, **kwargs):
        return None

    async def fake_aclose(self):
        return None

    monkeypatch.setattr(multicity, "gather", fake_gather)
    monkeypatch.setattr(multicity, "compose", fake_compose)

    class FakeLLM:
        async def aclose(self):
            return None

    monkeypatch.setattr(multicity, "LLMClient", lambda settings: FakeLLM())
    context = _context()
    plan = asyncio.run(
        multicity.build_multi_city_plan(
            providers=object(),
            settings=Settings(database_path="data/test.db"),
            context=context,
            trace=TurnTrace(request_id="test"),
            emit=emit,
        )
    )
    assert plan is not None
    assert len(plan.days) == 5
    assert [segment.destination["name"] for segment in plan.segments] == ["Kyoto", "Osaka"]
    assert not route_continuity_problems(plan, context)


# -- open-jaw trip costing, nights, legs and wording ---------------------


def _hk_context() -> TripContext:
    return TripContext(
        session_id="multi-hk",
        destination="Tokyo, Kyoto and Osaka",
        origin="Hong Kong",
        start_date="2026-12-14",
        end_date="2026-12-21",
        days=8,
        party_size=2,
        budget=30000,
        currency="HKD",
        stops=[TripStop(city="Tokyo", nights=3), TripStop(city="Kyoto", nights=2), TripStop(city="Osaka", nights=2)],
    )


def _hotel_plan(city: str, start: date, end: date) -> TripPlan:
    from app.domain.models import CostLine, HotelStay, Money

    plan = _plan(city, start, end)
    # What the single-city builder produces: the stay spans the segment's own days,
    # and each segment prices its own return flight home.
    plan.hotels = [
        HotelStay(
            id=f"hotel-{city}",
            name=f"{city} Inn",
            check_in=start.isoformat(),
            check_out=end.isoformat(),
            price_per_night=Money(amount=500, currency="HKD"),
        )
    ]
    plan.cost.lines = [
        CostLine(label="Travel", amount=4000, kind="flight", note="Return travel for the party."),
        CostLine(label="Stay", amount=1000, kind="hotel"),
    ]
    return plan


def _flight():
    from app.domain.models import Money, TicketOption

    return TicketOption(
        id="ticket-hk-tokyo",
        mode="flight",
        leg_id="leg-out",
        name="Hong Kong to Tokyo",
        origin="Hong Kong",
        destination="Tokyo",
        price=Money(amount=5000, currency="HKD"),
        selected=True,
    )


def _merged_hk():
    context = _hk_context()
    ranges = segment_ranges(context)
    plans = [_hotel_plan(span.city, span.start, span.end) for span in ranges]
    return context, ranges, _merge(plans, ranges, context, flights=[_flight()])


def test_open_jaw_trip_prices_one_flight_not_one_per_city():
    _context_, _ranges, plan = _merged_hk()
    flights = [line for line in plan.cost.lines if line.kind == "flight"]
    assert len(flights) == 1
    assert flights[0].amount == 5000
    assert [ticket.id for ticket in plan.tickets] == ["ticket-hk-tokyo"]


def test_hotel_stays_follow_the_requested_nights_and_touch_each_other():
    _context_, ranges, plan = _merged_hk()
    stays = [(date.fromisoformat(h.check_out) - date.fromisoformat(h.check_in)).days for h in plan.hotels]
    assert stays == [3, 2, 2]
    assert plan.hotels[0].check_in == "2026-12-14"
    assert plan.hotels[0].check_out == plan.hotels[1].check_in == "2026-12-17"
    assert plan.hotels[1].check_out == plan.hotels[2].check_in == "2026-12-19"
    assert plan.hotels[2].check_out == "2026-12-21"
    # The hotel cost uses those nights: 2 people share one room.
    stay_lines = [line for line in plan.cost.lines if line.kind == "hotel"]
    assert [line.amount for line in stay_lines] == [1500, 1000, 1000]
    assert len(ranges) == 3


def test_open_jaw_trip_has_outbound_between_and_return_legs():
    _context_, _ranges, plan = _merged_hk()
    assert [(leg.kind, leg.from_place, leg.to_place) for leg in plan.legs] == [
        ("outbound", "Hong Kong", "Tokyo"),
        ("between", "Tokyo", "Kyoto"),
        ("between", "Kyoto", "Osaka"),
        ("return", "Osaka", "Hong Kong"),
    ]
    assert plan.legs[0].depart_date == "2026-12-14"
    assert plan.legs[-1].depart_date == "2026-12-21"


def test_unpriced_parts_are_labelled_in_the_cost():
    _context_, _ranges, plan = _merged_hk()
    assert any("intercity transport" in note for note in plan.cost.notes)


def test_route_summary_uses_an_before_eight():
    _context_, _ranges, plan = _merged_hk()
    assert plan.summary.startswith("An 8-day route")
    from app.plan.builder import a_or_an

    assert [a_or_an(n) for n in (1, 7, 8, 11, 12, 18, 80, 81, 88, 100)] == [
        "A", "A", "An", "An", "A", "An", "An", "An", "An", "A",
    ]


def test_multi_city_builder_searches_flights_once_and_skips_them_per_segment(monkeypatch):
    import asyncio

    from app.config import Settings
    from app.core.trace import TurnTrace
    from app.domain.models import Money
    from app.evidence.models import Bundle
    from app.plan import multicity
    from app.plan.agents import Gathered

    gather_calls: list[dict] = []
    compose_origins: list[str] = []
    ticket_calls: list[tuple] = []

    async def fake_gather(**kwargs):
        gather_calls.append(kwargs)
        return Gathered(Bundle())

    async def fake_compose(*, context, **kwargs):
        compose_origins.append(context.origin)
        start = date.fromisoformat(context.start_date)
        end = date.fromisoformat(context.end_date)
        return _plan(context.destination, start, end), None

    class FakeProviders:
        async def tickets(self, origin, destination, start, end, party, **kwargs):
            from app.evidence.models import Evidence

            ticket_calls.append((origin, destination, start.isoformat(), end.isoformat()))
            return [
                Evidence(
                    ref="t1",
                    kind="ticket",
                    name=f"{origin} to {destination}",
                    price=Money(amount=5000, currency="HKD"),
                    price_kind="estimate",
                    tags=["flight"],
                )
            ]

    async def emit(*args, **kwargs):
        return None

    class FakeLLM:
        async def aclose(self):
            return None

    monkeypatch.setattr(multicity, "gather", fake_gather)
    monkeypatch.setattr(multicity, "compose", fake_compose)
    monkeypatch.setattr(multicity, "LLMClient", lambda settings: FakeLLM())
    plan = asyncio.run(
        multicity.build_multi_city_plan(
            providers=FakeProviders(),
            settings=Settings(database_path="data/test.db"),
            context=_hk_context(),
            trace=TurnTrace(request_id="test"),
            emit=emit,
        )
    )
    assert plan is not None
    # No segment is planned as its own trip home: no origin, no flight search.
    assert compose_origins == ["", "", ""]
    assert all(call.get("with_tickets") is False for call in gather_calls)
    assert ticket_calls == [("Hong Kong", "Tokyo", "2026-12-14", "2026-12-21")]
    assert len([line for line in plan.cost.lines if line.kind == "flight"]) == 1
    assert [leg.kind for leg in plan.legs] == ["outbound", "between", "between", "return"]
