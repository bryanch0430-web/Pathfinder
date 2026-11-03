"""Proposal DB-02 (p2 l.70-71, p3 l.108-111): a quick question is one model call, a
tool is called only if the plan does not already hold the answer, and the ask path
checks whether the question relates to the existing plan.

Each test runs one real turn through the turn service on the offline rig the
efficiency evaluation uses: a metered writer that counts every model call by
purpose, and sandbox providers counted where they run, below the toolbox, so a
tool call that skipped the log would still be counted.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from app.config import Settings
from app.domain.models import EvalCase, Intent
from app.eval_efficiency import ROUTER_PURPOSES, accepted_plan, build_rig, case_context
from app.eval_metering import RunLedger

QUESTION = "What is the weather in Kyoto on 12 April?"


def _april_trip_start() -> date:
    """A three-day Kyoto trip over 12 April that is always in the future."""
    return date(date.today().year + 1, 4, 11)


def _ask_turn(settings: Settings, question: str, *, forecast_on_day: bool = True, route: dict | None = None) -> dict:
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        session = ledger.store.create_session("")
        start = _april_trip_start()
        case = EvalCase(id="db02", dataset="efficiency", input=question, destination="Kyoto")
        context = case_context(case, session.id).model_copy(
            update={"start_date": start, "end_date": start + timedelta(days=2), "days": 3}
        )
        ledger.store.save_context(context)
        plan = asyncio.run(accepted_plan(settings, context))
        day = next(day for day in plan.days if (day.date.month, day.date.day) == (4, 12))
        assert day.weather is not None and day.weather.is_available
        if not forecast_on_day:
            day.weather = None
        ledger.store.save_plan(plan, "Accepted plan")
        rig.llm.route = route or {"intent": "ask", "confidence": 0.95}

        prompts: list[tuple[str, str]] = []
        complete = rig.llm.complete

        async def recording(**kwargs):
            prompts.append((kwargs["purpose"], kwargs["user"]))
            return await complete(**kwargs)

        rig.llm.complete = recording
        before = ledger.made.copy()
        events: list = []

        async def run() -> None:
            async for event in rig.turns.handle_turn(session_id=session.id, message=question):
                events.append(event)

        asyncio.run(run())
        trace = next(event.data for event in events if event.event == "trace")
        route_event = next(event.data for event in events if event.event == "route")
        reply = [m for m in ledger.store.list_messages(session.id, limit=20) if m.role == "assistant"][-1]
        return {
            "path": route_event["intent"],
            "purposes": list(rig.llm.purposes),
            "ask_calls": [p for p in rig.llm.purposes if p not in ROUTER_PURPOSES],
            "made": ledger.made - before,
            "logged": [row["tool"] for row in ledger.store.tool_calls(trace["request_id"])],
            "prompts": prompts,
            "reply": reply,
            "day": day.date,
            "errors": [event.data for event in events if event.event == "error"],
        }
    finally:
        ledger.close()


def test_db02_weather_already_in_the_plan_takes_one_model_call_and_no_tool(settings: Settings) -> None:
    turn = _ask_turn(settings, QUESTION)
    assert turn["errors"] == [] and turn["path"] == Intent.ASK.value
    # The rules route this clear question alone, so the whole turn is the one answer call.
    assert turn["purposes"] == ["ask.answer"]
    assert sum(turn["made"].values()) == 0 and turn["logged"] == []
    (purpose, prompt), = turn["prompts"]
    assert "Facts (source: plan)" in prompt and turn["day"].isoformat() in prompt
    assert turn["reply"].meta["source"] == "plan"
    assert turn["reply"].meta["related_to_plan"] is True


def test_db02_weather_missing_from_the_plan_takes_one_model_call_and_one_logged_tool(settings: Settings) -> None:
    turn = _ask_turn(settings, QUESTION, forecast_on_day=False)
    assert turn["errors"] == [] and turn["path"] == Intent.ASK.value
    assert turn["purposes"] == ["ask.answer"]
    assert dict(turn["made"]) == {"SandboxWeather.forecast": 1}
    assert turn["logged"] == ["weather.forecast"]
    (purpose, prompt), = turn["prompts"]
    assert "Facts (source: sandbox-weather)" in prompt and turn["day"].isoformat() in prompt
    assert turn["reply"].meta["source"] == "sandbox-weather"
    assert turn["reply"].meta["related_to_plan"] is True


def test_db02_a_model_routed_question_still_has_one_answer_call(settings: Settings) -> None:
    """With the rules first shortcut off, routing adds its own call; the ask path stays at one."""
    turn = _ask_turn(settings.model_copy(update={"router_rules_first": False}), QUESTION, forecast_on_day=False)
    assert turn["path"] == Intent.ASK.value
    assert any(p in ROUTER_PURPOSES for p in turn["purposes"])
    assert turn["ask_calls"] == ["ask.answer"]
    assert turn["logged"] == ["weather.forecast"]


def test_db02_a_date_outside_the_trip_is_not_answered_from_other_days(settings: Settings) -> None:
    """The plan does not hold 20 June when the trip is in April: one tool call for that date."""
    turn = _ask_turn(settings, "What is the weather in Kyoto on 20 June?")
    assert turn["path"] == Intent.ASK.value and turn["ask_calls"] == ["ask.answer"]
    assert turn["logged"] == ["weather.forecast"] and sum(turn["made"].values()) == 1
    (purpose, prompt), = turn["prompts"]
    june = date(_april_trip_start().year, 6, 20).isoformat()
    facts = prompt.split("Facts (", 1)[1]
    assert facts.startswith("source: sandbox-weather)") and f'"date":"{june}"' in facts
    assert turn["day"].isoformat() not in facts
    assert turn["reply"].meta["related_to_plan"] is False


def test_db02_a_question_unrelated_to_the_plan_is_flagged_and_not_answered_from_it(settings: Settings) -> None:
    turn = _ask_turn(settings, "What is the capital of France?")
    assert turn["errors"] == [] and turn["path"] == Intent.ASK.value
    assert turn["ask_calls"] == ["ask.answer"]
    assert sum(turn["made"].values()) == 0 and turn["logged"] == []
    (prompt,) = [user for purpose, user in turn["prompts"] if purpose == "ask.answer"]
    # The writer gets the plan outline for context but no itinerary rows to answer from.
    assert '"itinerary"' not in prompt
    assert turn["reply"].meta["related_to_plan"] is False
