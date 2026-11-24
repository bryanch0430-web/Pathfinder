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
from app.plan.builder import plan_token_cap
from app.plan.flight_windows import flight_boundary_problems, flight_windows


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
    # A same-day return: the departure that closes the day leaves the destination.
    plan.tickets[0].return_depart_at = "2027-04-10T16:00"
    plan.tickets[0].return_arrive_at = "2027-04-10T18:00"
    codes = {problem.code for problem in check_structure(plan, _context("2027-04-10"))}
    assert "after_flight_departure" in codes


def test_a_day_inside_the_flight_window_has_no_boundary_problem():
    plan = _plan("2027-04-10", "2027-04-10")
    plan.tickets[0].arrive_at = "2027-04-10T05:00"
    plan.tickets[0].depart_at = "2027-04-10T07:00"
    plan.tickets[0].return_depart_at = "2027-04-10T23:00"
    plan.days[0].items = [PlanItem(id="stop-1", type="attraction", name="Temple", start="11:00", end="12:00")]
    codes = {problem.code for problem in check_structure(plan, _context("2027-04-10"))}
    assert "before_flight_arrival" not in codes
    assert "after_flight_departure" not in codes


# -- the reported two-day Taipei plan ------------------------------------


def _taipei_ticket(*, one_way: bool = False) -> TicketOption:
    return TicketOption(
        id="tk-1",
        mode="flight",
        name="Hong Kong to Taipei · UO 114",
        origin="Hong Kong",
        destination="Taipei",
        depart_at="2026-12-04T15:00",
        arrive_at="2026-12-04T16:45",
        selected=True,
        return_depart_at="" if one_way else "2026-12-05T21:15",
        return_arrive_at="" if one_way else "2026-12-05T23:25",
    )


def _taipei_plan(*, one_way: bool = False) -> TripPlan:
    return TripPlan(
        trip_id="trip-test",
        session_id="session-test",
        destination=Destination(name="Taipei"),
        origin="Hong Kong",
        dates=DateRange(start="2026-12-04", end="2026-12-05"),
        party_size=1,
        tickets=[_taipei_ticket(one_way=one_way)],
        days=[
            DayPlan(
                date="2026-12-04",
                title="Arrival",
                items=[PlanItem(id="red-house", type="attraction", name="The Red House", start="20:05", end="20:55")],
            ),
            DayPlan(
                date="2026-12-05",
                title="Museum",
                items=[PlanItem(id="npm", type="attraction", name="Palace Museum", start="09:30", end="12:00")],
            ),
        ],
    )


def _taipei_context() -> TripContext:
    return TripContext(
        session_id="session-test",
        destination="Taipei",
        origin="Hong Kong",
        start_date="2026-12-04",
        end_date="2026-12-05",
        days=2,
    )


def test_the_arrival_day_has_no_departure_deadline():
    from datetime import date

    windows = flight_windows(_taipei_plan())
    assert windows[date(2026, 12, 4)] == (18 * 60 + 15, None, ["outbound arrival"])
    assert windows[date(2026, 12, 5)] == (None, 19 * 60 + 15, ["return departure"])


def test_an_evening_arrival_and_a_midday_return_pass():
    # The reported plan: dinner 18:45, sights to 21:51 on day 1, museum 09:30-12:00.
    plan = _taipei_plan()
    plan.days[0].items = [
        PlanItem(id="shin-yeh", type="meal", name="Shin Yeh", start="18:45", end="19:45"),
        PlanItem(id="red-house", type="attraction", name="The Red House", start="20:05", end="20:55"),
        PlanItem(id="liberty", type="attraction", name="Liberty Square", start="21:21", end="21:51"),
    ]
    assert flight_boundary_problems(plan, _taipei_context()) == []


def test_an_item_before_the_arrival_is_still_refused():
    plan = _taipei_plan()
    plan.days[0].items = [PlanItem(id="early", type="attraction", name="Early museum", start="10:00", end="11:00")]
    assert [problem.code for problem in flight_boundary_problems(plan, _taipei_context())] == [
        "before_flight_arrival"
    ]


def test_an_item_after_the_return_departure_buffer_is_still_refused():
    plan = _taipei_plan()
    plan.days[1].items = [PlanItem(id="late", type="meal", name="Late dinner", start="20:00", end="21:00")]
    assert [problem.code for problem in flight_boundary_problems(plan, _taipei_context())] == [
        "after_flight_departure"
    ]


def test_a_one_way_trip_has_no_return_deadline():
    from datetime import date

    assert flight_windows(_taipei_plan(one_way=True)).get(date(2026, 12, 5)) is None


def test_the_plan_answer_budget_has_room_for_a_verbose_short_plan():
    assert plan_token_cap(1) == 8000
    assert plan_token_cap(2) == 8000
    assert plan_token_cap(4) == 9000
    assert plan_token_cap(7) == 13500
    assert plan_token_cap(30) == 16000
