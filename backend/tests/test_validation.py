"""B6: a repair re-shapes a plan; it cannot drop a stop or refresh a fetch time."""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest

from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.validation import repair_plan
from app.config import Settings
from app.domain.errors import PlanInvalidError
from app.domain.models import EvalCase, TripPlan, utcnow
from app.eval_efficiency import accepted_plan, case_context
from app.eval_metering import MeteredLLM
from app.infrastructure.observability import NoopTracer

ERRORS = ["day 1: an item has no source"]


def _plan(settings: Settings) -> TripPlan:
    context = case_context(EvalCase(id="t", dataset="design", input="", destination="Kyoto"), "s")
    return asyncio.run(accepted_plan(settings, context))


def _gateway(settings: Settings, reply: str) -> tuple[LLMGateway, MeteredLLM]:
    llm = MeteredLLM(replies={"plan.repair": reply})
    return LLMGateway(llm, PromptLibrary(settings), NoopTracer(), settings), llm


def test_repair_that_drops_a_stop_is_rejected(settings: Settings) -> None:
    plan = _plan(settings)
    repaired = plan.model_copy(deep=True)
    dropped = repaired.days[0].items.pop()
    gateway, llm = _gateway(settings, repaired.model_dump_json())

    with pytest.raises(PlanInvalidError) as failure:
        asyncio.run(repair_plan(plan, ERRORS, gateway, settings))

    # Every attempt came back without the stop, so none was accepted, and the error names it.
    assert llm.purposes == ["plan.repair"] * settings.max_repairs
    assert dropped.name in str(failure.value)


def test_repair_keeps_the_fetch_time_and_sources_the_writer_left_out(settings: Settings) -> None:
    plan = _plan(settings)
    aged = utcnow() - timedelta(days=3)
    for day in plan.days:
        for item in day.items:
            item.fetched_at = aged
        if day.weather is not None:
            day.weather.fetched_at = aged
    for hotel in plan.hotels:
        hotel.fetched_at = aged
    for ticket in plan.tickets:
        ticket.fetched_at = aged
    meal = next(item for _, item in plan.iter_items() if item.type.value == "meal")
    assert meal.source_refs and plan.hotels and plan.tickets

    # The writer returns the plan without any fetch time, and without the meals' sources.
    payload = json.loads(plan.model_dump_json())
    for day in payload["days"]:
        for item in day["items"]:
            item.pop("fetched_at")
            if item["type"] == "meal":
                item.pop("source_refs")
        if day.get("weather"):
            day["weather"].pop("fetched_at")
    for row in payload["hotels"] + payload["tickets"]:
        row.pop("fetched_at")
    gateway, _ = _gateway(settings, json.dumps(payload))

    repaired = asyncio.run(repair_plan(plan, ERRORS, gateway, settings))

    assert all(item.fetched_at == aged for _, item in repaired.iter_items())
    assert all(day.weather is None or day.weather.fetched_at == aged for day in repaired.days)
    assert all(row.fetched_at == aged for row in [*repaired.hotels, *repaired.tickets])
    kept_meal = next(item for _, item in repaired.iter_items() if item.name == meal.name)
    assert [ref.url for ref in kept_meal.source_refs] == [ref.url for ref in meal.source_refs]
