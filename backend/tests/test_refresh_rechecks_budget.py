"""A refresh that prices the plan again reads the budget check again.

Before, only a section that was filled in (it had never arrived) had the plan's derived checks read again. Hotel prices
refreshed after they went out of date changed the cost to "over" next to a check that still passed on the old total.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from app.application.freshness import FreshnessService
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.models import ConstraintStatus, Money
from app.evaluation import SandboxHotels
from app.infrastructure.observability import NoopTracer
from tests.test_modify_targets import _build


class _Quote(SandboxHotels):
    """The hotel source with every rate multiplied by ``factor``."""

    def __init__(self, destination: str, factor: float) -> None:
        super().__init__(destination)
        self.factor = factor

    async def search(self, **kwargs):
        rows = await super().search(**kwargs)
        for row in rows:
            row.nightly_price = Money(amount=row.nightly_price.amount * self.factor, currency="USD")
        return rows


def _plan_with_a_budget(settings: Settings, budget: float):
    gateway, planner, plan_path, context, _ = _build(settings)
    context.budget, context.currency = budget, "USD"
    toolbox = Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="t")
    plan = asyncio.run(
        plan_path.run(context=context, session_id="s", toolbox=toolbox, tracer=NoopTracer(), emit=lambda *_: None)
    )
    return planner, plan_path, context, plan


def _refresh(settings: Settings, planner, plan_path, context, plan, factor: float):
    plan_path.registry.hotels = [_Quote("Kyoto", factor)]
    service = FreshnessService(
        registry=plan_path.registry,
        planner=planner,
        settings=settings,
        clock=lambda: plan.updated_at + timedelta(days=8),
    )
    outcome = asyncio.run(service.check(plan, context=context, tracer=NoopTracer(), request_id="r"))
    assert outcome is not None and "hotels" in outcome.refreshed
    return outcome.plan


def _budget_check(plan):
    return next(check for check in plan.constraints if check.text.startswith("Stay within"))


def test_prices_refreshed_over_the_budget_fail_the_budget_check(settings: Settings) -> None:
    planner, plan_path, context, plan = _plan_with_a_budget(settings, 700)
    assert plan.cost.status == "within" and _budget_check(plan).status is ConstraintStatus.PASS

    refreshed = _refresh(settings, planner, plan_path, context, plan, 3.0)

    assert refreshed.cost.status == "over" and refreshed.cost.total > 700
    check = _budget_check(refreshed)
    assert check.status is ConstraintStatus.FAIL
    assert f"{refreshed.cost.total:,.2f}" in check.evidence, "the check reads the new total"


def test_prices_refreshed_under_the_budget_pass_the_budget_check_with_the_new_total(settings: Settings) -> None:
    planner, plan_path, context, plan = _plan_with_a_budget(settings, 700)

    refreshed = _refresh(settings, planner, plan_path, context, plan, 0.5)

    assert refreshed.cost.status == "within"
    check = _budget_check(refreshed)
    assert check.status is ConstraintStatus.PASS
    assert f"{refreshed.cost.total:,.2f}" in check.evidence
    assert f"{plan.cost.total:,.2f}" not in check.evidence


def test_the_other_checks_of_the_plan_are_left_as_they_were(settings: Settings) -> None:
    planner, plan_path, context, plan = _plan_with_a_budget(settings, 700)

    refreshed = _refresh(settings, planner, plan_path, context, plan, 3.0)

    before = {check.text: (check.status, check.evidence) for check in plan.constraints}
    after = {check.text: (check.status, check.evidence) for check in refreshed.constraints}
    assert set(before) == set(after)
    assert {text for text in before if before[text] != after[text]} <= {_budget_check(plan).text}
