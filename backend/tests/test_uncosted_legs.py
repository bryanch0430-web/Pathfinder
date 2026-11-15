"""The cost notes name every leg that is not costed: an estimate, a leg with no fare and a leg with no option at all."""

from __future__ import annotations

from datetime import date, datetime

from app.application.planner import Planner
from app.domain.models import DateRange, TicketOption, TripContext
from app.infrastructure.providers.fx import FxService
from tests.plan_samples import make_two_city_plan


def _context() -> TripContext:
    return TripContext(
        session_id="s2",
        destination="Kyoto",
        budget=2000,
        currency="HKD",
        origin="Hong Kong",
        party_size=2,
    )


def test_cost_notes_name_a_leg_that_has_no_option_at_all(settings):
    plan = make_two_city_plan()
    plan.tickets = [t for t in plan.tickets if t.leg_id != "leg_home"]
    planner = Planner(None, settings, FxService())  # type: ignore[arg-type]
    notes = planner.cost_for_plan(_context(), plan).notes
    assert any("Osaka to Hong Kong" in note and "not costed" in note for note in notes)
    assert any("Kyoto to Osaka" in note and "not costed" in note for note in notes)


def test_a_one_leg_trip_says_an_estimated_journey_is_an_estimate(settings):
    planner = Planner(None, settings, FxService())  # type: ignore[arg-type]
    ground = TicketOption(
        id="t_train",
        mode="train",
        name="Osaka to Kyoto by train",
        depart_at=datetime(2026, 12, 3, 9, 0),
        arrive_at=datetime(2026, 12, 3, 10, 0),
        estimated=True,
        selected=True,
    )
    dates = DateRange(start=date(2026, 12, 3), end=date(2026, 12, 5))
    notes = planner._cost(_context(), None, [ground], dates).notes
    assert any("estimate" in note and "not costed" in note for note in notes)
    assert not any("Flight fare was not returned" in note for note in notes)
