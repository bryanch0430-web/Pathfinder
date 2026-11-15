"""A city's budget share is worked out by one function for planning and for edits, and the cost notes name every leg
that is not costed."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.application.budget_share import city_budget_shares
from app.application.modification import EditResult
from app.application.multi_edit import edit_cities
from app.application.planner import Planner
from app.domain.models import Money, TripContext
from app.infrastructure.providers.fx import FxService
from tests.plan_samples import make_two_city_plan


class _Fx:
    """Converts JPY to HKD at 0.05 and leaves everything else as it is."""

    def convert(self, amount: float, from_currency: str, to_currency: str) -> float:
        if from_currency.upper() == "JPY" and to_currency.upper() == "HKD":
            return amount * 0.05
        return amount


def _priced(plan, ticket_id: str, amount: float, currency: str = "HKD"):
    for ticket in plan.tickets:
        if ticket.id == ticket_id:
            ticket.price = Money(amount=amount, currency=currency)
    return plan


def test_shares_take_the_priced_legs_off_first_and_split_the_rest_by_nights():
    plan = _priced(make_two_city_plan(), "t_out_1", 400)
    chosen = plan.chosen_tickets()
    shares = city_budget_shares(2000, "HKD", [2, 3], chosen, _Fx())
    assert shares[0] == pytest.approx(640)
    assert shares[1] == pytest.approx(960)


def test_shares_convert_a_fare_in_another_currency():
    plan = _priced(make_two_city_plan(), "t_out_1", 8000, "JPY")
    shares = city_budget_shares(2000, "HKD", [2, 2], plan.chosen_tickets(), _Fx())
    assert shares[0] == pytest.approx(800)
    assert shares[1] == pytest.approx(800)


def test_shares_are_none_without_a_budget_and_never_below_zero():
    plan = _priced(make_two_city_plan(), "t_out_1", 5000)
    assert city_budget_shares(None, "HKD", [2, 2], plan.chosen_tickets(), _Fx()) == [
        None,
        None,
    ]
    shares = city_budget_shares(1000, "HKD", [2, 2], plan.chosen_tickets(), _Fx())
    assert shares[0] == pytest.approx(0)
    assert shares[1] == pytest.approx(0)


class _RecordingModify:
    """A ModifyPath stand-in that records the budget each city is edited with and changes nothing."""

    def __init__(self, settings):
        self.planner = Planner(None, settings, FxService())  # type: ignore[arg-type]
        self.budgets: list[float | None] = []

    async def run(self, *, plan, context, **_):
        self.budgets.append(context.budget)
        return EditResult(plan, [])


def test_an_edit_gives_each_city_the_share_planning_gives_it(settings):
    plan = _priced(make_two_city_plan(), "t_out_1", 400)
    context = TripContext(
        session_id="s2",
        destination="Kyoto",
        budget=2000,
        currency="HKD",
        origin="Hong Kong",
        party_size=2,
    )
    modify = _RecordingModify(settings)
    asyncio.run(
        edit_cities(
            modify,  # type: ignore[arg-type]
            plan=plan,
            context=context,
            message="make every day more relaxed",
            draft=SimpleNamespace(target_day=None),
            route_parts=[],
            toolbox=SimpleNamespace(),
            tracer=None,
            emit=lambda *_: None,
        )
    )
    planned = city_budget_shares(
        2000, "HKD", [2, 2], plan.chosen_tickets(), modify.planner.fx
    )
    assert len(modify.budgets) == 2
    for edited, share in zip(modify.budgets, planned):
        assert edited == pytest.approx(share)
    # 2000 less the 400 flight, over 4 nights, 2 nights each.
    assert modify.budgets[0] == pytest.approx(800)
