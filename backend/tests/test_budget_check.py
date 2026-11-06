"""A budget that cannot cover the trip is asked about before any agent runs (a zero budget, or one below
``min_budget_per_person_night`` per traveller per night, converted from US dollars), "plan anyway" is kept so the
question is not asked again, a start date that has gone by is asked about the same way, and the clarify card's budget
options are sized to the trip's nights and travellers.

Offline: the sandbox providers and the metered writer, as the rest of the suite.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any, Callable

import pytest

from app.application.budget_check import budget_options, budget_question, floor_per_person_night
from app.application.orchestrator import _slot_options
from app.application.understanding import understand
from app.config import Settings
from app.domain.models import EvalCase, TripContext
from app.eval_efficiency import build_rig, case_context
from app.eval_metering import RunLedger
from app.infrastructure.providers.fx import FxService


# -- 6. a budget that cannot cover the trip is asked about before any agent runs ----------------------------------


def _fresh(
    settings: Settings,
    scenario: Callable[..., Any],
    *,
    budget: float,
    currency: str = "HKD",
    days: int = 13,
    party: int = 2,
    start: date | None = None,
) -> None:
    settings = settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})

    async def main() -> None:
        ledger = RunLedger()
        try:
            rig = build_rig(settings, "Kyoto", ledger)
            session = ledger.store.create_session("guest-budget")
            case = EvalCase(
                id="b", dataset="design", input="", destination="Kyoto", days=days, party_size=party,
                budget=budget, currency=currency,
            )
            context = case_context(case, session.id)
            if start is not None:
                context.start_date, context.end_date = start, start + timedelta(days=days - 1)
            ledger.store.save_context(context)
            rig.llm.route = {"intent": "plan", "confidence": 0.95}
            await scenario(rig, ledger, session.id)
        finally:
            ledger.close()

    asyncio.run(main())


async def _turn(rig, session_id: str, message: str) -> list:
    return [event async for event in rig.turns.handle_turn(session_id=session_id, message=message)]


def _clarify(events: list) -> dict | None:
    return next((event.data for event in events if event.event == "clarify"), None)


def test_a_budget_too_small_for_the_trip_is_asked_about_and_no_agent_runs(settings: Settings) -> None:
    async def scenario(rig, ledger, session_id: str) -> None:
        events = await _turn(rig, session_id, "Plan my trip.")

        asked = _clarify(events)
        assert asked is not None, [event.event for event in events]
        assert "HKD 5,000 for 2 people over 12 nights is about HKD 208 a night per person" in asked["question"]
        assert asked["slots"] == ["budget"]
        assert asked["options"]["budget"] == ["HKD 42,000", "Keep HKD 5,000 and plan anyway"]
        assert not [event for event in events if event.event in {"plan", "error"}]
        assert sum(ledger.made.values()) == 0, ledger.made
        assert ledger.store.get_plan(session_id) is None
        stored = ledger.store.list_messages(session_id, limit=4)
        assert stored[-1].role == "assistant" and stored[-1].content == asked["question"]

    _fresh(settings, scenario, budget=5000)


def test_plan_anyway_plans_and_does_not_ask_again(settings: Settings) -> None:
    async def scenario(rig, ledger, session_id: str) -> None:
        await _turn(rig, session_id, "Plan my trip.")

        events = await _turn(rig, session_id, "Here are the missing details — budget: Keep HKD 5,000 and plan anyway.")

        assert _clarify(events) is None, _clarify(events)
        assert [event for event in events if event.event == "plan"]
        context = ledger.store.get_context(session_id)
        assert context.budget == 5000 and context.budget_confirmed is True
        again = await _turn(rig, session_id, "Plan my trip.")
        assert _clarify(again) is None

    _fresh(settings, scenario, budget=5000)


def test_the_suggested_budget_answer_plans(settings: Settings) -> None:
    async def scenario(rig, ledger, session_id: str) -> None:
        await _turn(rig, session_id, "Plan my trip.")

        events = await _turn(rig, session_id, "Here are the missing details — budget: HKD 42,000.")

        assert _clarify(events) is None
        assert ledger.store.get_context(session_id).budget == 42000
        assert [event for event in events if event.event == "plan"]

    _fresh(settings, scenario, budget=5000)


def test_a_normal_budget_is_not_asked_about_and_makes_the_same_model_calls(settings: Settings) -> None:
    purposes: dict[str, list[str]] = {}

    def run(label: str, floor: float) -> None:
        async def scenario(rig, ledger, session_id: str) -> None:
            events = await _turn(rig, session_id, "Plan my trip.")
            assert _clarify(events) is None
            assert [event for event in events if event.event == "plan"]
            purposes[label] = list(rig.llm.purposes)

        _fresh(settings.model_copy(update={"min_budget_per_person_night": floor}), scenario, budget=60000, days=3)

    run("checked", 150)
    run("unchecked", 0)
    assert purposes["checked"] == purposes["unchecked"]


def test_a_zero_budget_is_asked_about() -> None:
    start = date.today() + timedelta(days=30)
    context = TripContext(
        session_id="s", destination="Kyoto", origin="Hong Kong", start_date=start,
        end_date=start + timedelta(days=4), days=5, party_size=2, budget=0, currency="HKD",
    )
    asked = budget_question(context, floor=1170.0, today=date.today())
    assert asked is not None and asked.slots == ["budget"]
    assert "Keep HKD 0 and plan anyway" in asked.options["budget"]
    context.budget_confirmed = True
    assert budget_question(context, floor=1170.0, today=date.today()) is None


def test_a_past_start_date_is_asked_about_with_next_years_date() -> None:
    today = date.today()
    start = today - timedelta(days=40)
    context = TripContext(
        session_id="s", destination="Kyoto", origin="Hong Kong", start_date=start,
        end_date=start + timedelta(days=2), days=3, party_size=2, budget=60000, currency="HKD",
    )
    asked = budget_question(context, floor=1170.0, today=today)

    assert asked is not None and asked.slots == ["dates"]
    assert "The start date is in the past" in asked.question
    following = start.replace(year=start.year + 1)
    assert asked.options["dates"][0] == f"{following.isoformat()} to {(following + timedelta(days=2)).isoformat()}"
    context.past_dates_confirmed = True
    assert budget_question(context, floor=1170.0, today=today) is None


def test_the_answers_to_the_question_are_read_back(offline_gateway) -> None:
    start = date.today() + timedelta(days=30)
    context = TripContext(
        session_id="s", destination="Kyoto", origin="Hong Kong", start_date=start,
        end_date=start + timedelta(days=12), days=13, party_size=2, budget=5000, currency="HKD",
    )
    kept = asyncio.run(understand("Here are the missing details — budget: Keep HKD 5,000 and plan anyway.", context, offline_gateway))
    assert (kept.budget, kept.currency, kept.budget_confirmed) == (5000, "HKD", True)
    raised = asyncio.run(understand("Here are the missing details — budget: HKD 42,000.", context, offline_gateway))
    assert (raised.budget, raised.budget_confirmed) == (42000, False)
    # A confirmation is for the budget it was given for: another budget is checked again.
    moved = asyncio.run(understand("Here are the missing details — budget: HKD 3,000.", kept, offline_gateway))
    assert (moved.budget, moved.budget_confirmed) == (3000, False)
    past = context.model_copy(update={"start_date": date.today() - timedelta(days=40)})
    kept_dates = asyncio.run(
        understand("Here are the missing details — dates: Keep these dates and plan anyway.", past, offline_gateway)
    )
    assert kept_dates.past_dates_confirmed is True and kept_dates.start_date == past.start_date



# -- 7. the budget options scale with the trip ------------------------------------------------------------------


def _amounts(options: list[str]) -> list[int]:
    return [int(text.split()[-1].replace(",", "")) for text in options]


def test_the_budget_options_scale_with_nights_and_party() -> None:
    start = date.today() + timedelta(days=30)
    short = TripContext(session_id="s", start_date=start, end_date=start + timedelta(days=2), days=3, party_size=1, currency="HKD")
    long = TripContext(session_id="s", start_date=start, end_date=start + timedelta(days=12), days=13, party_size=2, currency="HKD")

    assert budget_options(long, 1170.0) == ["HKD 28,000", "HKD 56,000", "HKD 98,000"]
    assert _amounts(budget_options(short, 1170.0)) == [2300, 4700, 8200]
    assert _slot_options(["budget"], long, 1170.0)["budget"] == budget_options(long, 1170.0)


def test_the_missing_budget_question_offers_options_for_this_trip(settings: Settings) -> None:
    async def scenario(rig, ledger, session_id: str) -> None:
        context = ledger.store.get_context(session_id)
        context.budget = None
        ledger.store.save_context(context)

        events = await _turn(rig, session_id, "Plan my trip.")

        asked = _clarify(events)
        assert asked is not None and "budget" in asked["slots"]
        assert asked["options"]["budget"] == ["HKD 28,000", "HKD 56,000", "HKD 98,000"]

    _fresh(settings, scenario, budget=5000)


# -- the floor, the trip form and the prompts -------------------------------------------------------------------


def test_the_floor_is_converted_from_us_dollars_and_off_for_a_currency_without_a_rate(settings: Settings) -> None:
    fx = FxService()
    assert floor_per_person_night(settings, fx, "USD") == 150
    assert floor_per_person_night(settings, fx, "HKD") == pytest.approx(fx.convert(150, "USD", "HKD"))
    assert floor_per_person_night(settings, fx, "HKD") > 1000
    # No rate for the currency: 150 of it would be no floor at all, so the check is off rather than wrong.
    assert floor_per_person_night(settings, fx, "XYZ") == 0
    assert floor_per_person_night(settings.model_copy(update={"min_budget_per_person_night": 0}), fx, "HKD") == 0


def test_a_budget_changed_in_the_trip_form_is_checked_again(settings: Settings) -> None:
    from fastapi.testclient import TestClient

    from app.interface.api import create_app

    guest = {"X-Client-Token": "test"}
    with TestClient(create_app(settings)) as client:
        session_id = client.post("/api/journeys", json={"client_token": "test"}).json()["session"]["id"]
        client.patch(f"/api/journeys/{session_id}/state", json={"budget": 5000, "currency": "HKD"}, headers=guest)
        store = client.app.state.container.store
        context = store.get_context(session_id)
        context.budget_confirmed = True
        store.save_context(context)

        client.patch(f"/api/journeys/{session_id}/state", json={"party_size": 3}, headers=guest)
        assert store.get_context(session_id).budget_confirmed is True
        client.patch(f"/api/journeys/{session_id}/state", json={"budget": 6000}, headers=guest)
        assert store.get_context(session_id).budget_confirmed is False


def test_the_marks_of_the_check_stay_out_of_the_prompts() -> None:
    from app.application.prompt_context import trip_state

    context = TripContext(session_id="s", destination="Kyoto", budget=5000, budget_confirmed=True)
    assert "confirmed" not in trip_state(context)
