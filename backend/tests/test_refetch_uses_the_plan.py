"""A section fetched again is asked about the trip the PLAN is for, never about what the trip's form says by now.

The freshness check built its request from the stored form and let the form's destination win. The form says
something else after a Save without a re-plan, and after a plain question about another city (the
understanding step stores that city as the form's destination), so the stay and the fares of that city were
saved into the plan of the first one as the next version, and the cost was worked out from them. The budget and
the currency came from the form as well, so the refreshed cost no longer agreed with the plan's own budget.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient

from app.application.freshness import SECTIONS
from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner
from app.config import Settings
from app.domain.models import TripContext
from app.eval_efficiency import cost_sandbox
from app.eval_metering import RunLedger
from app.infrastructure.observability import NoopTracer
from app.interface.api import create_app
from tests.test_failed_sections import Switch, _chat, _context, _planned, _planned_in_chat, _scene, _switches


def _record(sources: dict[str, Switch]) -> dict[str, list[str]]:
    """What the hotel and the flight sources are asked about from now on."""
    asked: dict[str, list[str]] = {"hotels": [], "tickets": []}
    for name, log in asked.items():
        inner = sources[name].search

        async def search(*, _inner: Any = inner, _log: list[str] = log, **kwargs: Any) -> Any:
            _log.append(kwargs["destination"])
            return await _inner(**kwargs)

        setattr(sources[name], "search", search)
    return asked


def _all_up(sources: dict[str, Switch]) -> None:
    for source in sources.values():
        source.down = False


def test_a_section_that_never_arrived_is_asked_about_the_plans_destination_not_the_forms(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    scene = _scene(settings, offline_gateway)
    _all_up(scene.sources)
    asked = _record(scene.sources)
    scene.clock[0] = scene.plan.updated_at + timedelta(days=1)
    form = _context().model_copy(update={"destination": "Tokyo"})

    outcome = asyncio.run(
        scene.service.check(scene.plan, context=form, tracer=NoopTracer(), request_id="req_form", save=False)
    )

    assert outcome is not None and sorted(outcome.refreshed) == sorted(SECTIONS)
    assert scene.plan.destination.name == "Kyoto"
    assert asked == {"hotels": ["Kyoto"], "tickets": ["Kyoto"]}


def test_the_plans_budget_and_currency_stand_when_a_section_is_fetched_again(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    scene = _scene(settings, offline_gateway)
    _all_up(scene.sources)
    scene.clock[0] = scene.plan.updated_at + timedelta(days=1)
    assert scene.plan.budget is not None
    form = _context().model_copy(update={"budget": 100.0, "currency": "EUR"})

    outcome = asyncio.run(
        scene.service.check(scene.plan, context=form, tracer=NoopTracer(), request_id="req_budget", save=False)
    )

    assert outcome is not None
    shown = outcome.plan
    assert shown.cost.currency == scene.plan.cost.currency != "EUR"
    assert shown.cost.budget == scene.plan.budget.amount
    assert shown.cost.total > scene.plan.cost.total, "the stay and the fares that came back are costed"
    budget_check = next(check for check in shown.constraints if check.text.startswith("Stay within"))
    assert f"{shown.cost.currency} {shown.cost.total:,.2f}" in budget_check.evidence, "the check reads the new cost"


def test_a_form_without_a_stored_copy_still_gets_the_plans_budget_and_currency(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    scene = _scene(settings, offline_gateway)

    asked = scene.service._agent_context(scene.plan, None)

    assert (asked.destination, asked.currency, asked.budget) == (
        scene.plan.destination.name,
        scene.plan.cost.currency,
        scene.plan.budget.amount if scene.plan.budget else None,
    )


def test_reopening_a_trip_whose_form_was_saved_with_another_city_keeps_to_the_planned_city(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    registry = cost_sandbox(settings, "Kyoto")
    sources = _switches(registry)
    with TestClient(create_app(settings)) as client:
        container = client.app.state.container
        guest = {"X-Client-Token": "guest-refetch-plan"}
        session_id = client.post("/api/journeys", json={"client_token": "guest-refetch-plan"}).json()["session"]["id"]
        freshness = container.turn_service.freshness
        freshness.registry = registry
        container.store.save_context(_context(session_id))
        plan = asyncio.run(_planned(registry, Planner(offline_gateway, settings, registry.fx), settings, session_id))
        container.store.save_plan(plan, "Initial plan")
        _all_up(sources)
        asked = _record(sources)
        saved = client.patch(f"/api/journeys/{session_id}/state", json={"destination": "Tokyo"}, headers=guest)
        assert saved.status_code == 200, saved.text
        freshness.clock = lambda: plan.updated_at + timedelta(days=1)

        opened = client.get(f"/api/journeys/{session_id}", headers=guest).json()

    shown = opened["plan"]
    assert shown["destination"]["name"] == "Kyoto"
    assert shown["version"] == plan.version + 1
    assert opened["context"]["destination"] == "Tokyo", "the form is the traveller's to change, and is left as saved"
    assert asked == {"hotels": ["Kyoto"], "tickets": ["Kyoto"]}


def test_a_question_about_another_city_does_not_fetch_that_citys_stay_into_the_plan(settings: Settings) -> None:
    ledger = RunLedger()
    try:
        rig, sources, session_id = _planned_in_chat(settings, ledger)
        planned = ledger.store.get_plan(session_id)
        _all_up(sources)
        asked = _record(sources)
        rig.turns.freshness.clock = lambda: planned.updated_at + timedelta(days=1)
        rig.llm.route = {"intent": "ask", "confidence": 0.95}

        _chat(rig, session_id, "What is the weather like in Osaka?")
        form = ledger.store.get_context(session_id)
        stored = ledger.store.get_plan(session_id)
    finally:
        ledger.close()

    assert isinstance(form, TripContext) and form.destination == "Osaka", "the question moved the form to Osaka"
    assert stored.destination.name == "Kyoto"
    assert asked == {"hotels": ["Kyoto"], "tickets": ["Kyoto"]}
