"""Modify-path merge: a "cheaper" hotel swap never picks a pricier hotel (DECISIONS §5.7)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from backend.agents.merge import merge_modify
from backend.agents.route_order import NearestNeighbourOrderer
from backend.schemas.agents import AgentResult, HotelData
from backend.schemas.common import AgentName, Money, SectionStatus
from backend.schemas.routing import ChangeRequest
from backend.schemas.trip import TripContext
from backend.schemas.trip_plan import DayPlan, Hotel, HotelStay, SourceRef, TripPlan

START, END = date(2026, 4, 10), date(2026, 4, 12)
SOURCE = SourceRef(tool="places", call_id="tc_1", provider="mock-places", fetched_at=datetime(2026, 4, 1, tzinfo=UTC))


def _hotel(hotel_id: str, price: float, currency: str = "JPY") -> Hotel:
    return Hotel(hotel_id=hotel_id, name=hotel_id.title(), nightly_price=Money(amount=price, currency=currency), source=SOURCE)


def _context() -> TripContext:
    return TripContext(
        destination="Kyoto", start_date=START, end_date=END, party_size=2, budget=Money(amount=300_000, currency="JPY")
    )


def _merge(current: Hotel, candidates: list[Hotel], change: ChangeRequest) -> TripPlan:
    plan = TripPlan(
        plan_id="p1",
        destination="Kyoto",
        start_date=START,
        end_date=END,
        party_size=2,
        budget=Money(amount=300_000, currency="JPY"),
        days=[DayPlan(date=START), DayPlan(date=date(2026, 4, 11)), DayPlan(date=END)],
        hotel=HotelStay(hotel=current, check_in=START, check_out=END),
    )
    results = {
        AgentName.HOTEL: AgentResult(agent=AgentName.HOTEL, status=SectionStatus.OK, data=HotelData(candidates=candidates))
    }
    return merge_modify(plan, results, context=_context(), change=change, orderer=NearestNeighbourOrderer()).plan


CHEAPER = ChangeRequest(replace_hotel=True, cheaper_hotel=True)


def test_cheaper_swap_skips_a_better_ranked_pricier_hotel() -> None:
    # The agent ranks the pricier hotel first; a plain swap would take it.
    candidates = [_hotel("grand", 20_000), _hotel("hostel", 4_000)]
    plain = _merge(_hotel("mid", 9_000), candidates, ChangeRequest(replace_hotel=True))
    assert plain.hotel is not None and plain.hotel.hotel.hotel_id == "grand"

    merged = _merge(_hotel("mid", 9_000), candidates, CHEAPER)
    assert merged.hotel is not None and merged.hotel.hotel.hotel_id == "hostel"


def test_cheaper_swap_keeps_the_current_hotel_when_nothing_is_cheaper() -> None:
    merged = _merge(_hotel("mid", 9_000), [_hotel("grand", 20_000), _hotel("same", 9_000)], CHEAPER)
    assert merged.hotel is not None and merged.hotel.hotel.hotel_id == "mid"


def test_cheaper_swap_ignores_prices_in_another_currency() -> None:
    merged = _merge(_hotel("mid", 9_000), [_hotel("hk", 300, currency="HKD")], CHEAPER)
    assert merged.hotel is not None and merged.hotel.hotel.hotel_id == "mid"
