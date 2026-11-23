from __future__ import annotations

from app.domain.models import (
    DateRange,
    DayPlan,
    Destination,
    PlanItem,
    TicketOption,
    TripContext,
    TripPlan,
)
from app.plan.validator import check_structure


def _plan(start: str, end: str) -> TripPlan:
    return TripPlan(
        trip_id="trip-test",
        session_id="session-test",
        destination=Destination(name="Kyoto"),
        dates=DateRange(start=start, end=start),
        days=[
            DayPlan(
                date=start,
                items=[
                    PlanItem(id="stop-1", type="attraction", name="Temple", start="09:00", end="10:00"),
                    PlanItem(id="stop-2", type="meal", name="Lunch", start="18:00", end="19:00"),
                ],
            )
        ],
        tickets=[
            TicketOption(
                id="flight-1",
                mode="flight",
                name="HKG → KIX",
                depart_at=f"{start}T07:00",
                arrive_at=f"{start}T12:00",
                selected=True,
            )
        ],
    )


def _context(start: str) -> TripContext:
    return TripContext(
        session_id="session-test",
        destination="Kyoto",
        start_date=start,
        end_date=start,
        days=1,
    )


def test_flight_arrival_blocks_early_stops():
    plan = _plan("2027-04-10", "2027-04-10")
    codes = {problem.code for problem in check_structure(plan, _context("2027-04-10"))}
    assert "before_flight_arrival" in codes


def test_flight_departure_blocks_late_stops():
    plan = _plan("2027-04-10", "2027-04-10")
    plan.tickets[0].arrive_at = "2027-04-10T05:00"
    plan.tickets[0].depart_at = "2027-04-10T16:00"
    codes = {problem.code for problem in check_structure(plan, _context("2027-04-10"))}
    assert "after_flight_departure" in codes


def test_a_day_inside_the_flight_window_has_no_boundary_problem():
    plan = _plan("2027-04-10", "2027-04-10")
    plan.tickets[0].arrive_at = "2027-04-10T05:00"
    plan.tickets[0].depart_at = "2027-04-10T23:00"
    plan.days[0].items = [PlanItem(id="stop-1", type="attraction", name="Temple", start="11:00", end="12:00")]
    codes = {problem.code for problem in check_structure(plan, _context("2027-04-10"))}
    assert "before_flight_arrival" not in codes
    assert "after_flight_departure" not in codes
