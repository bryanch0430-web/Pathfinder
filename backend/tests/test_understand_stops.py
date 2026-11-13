"""Several cities in one message become ordered stops on the trip form."""

from __future__ import annotations

import asyncio
import json

from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.understanding import understand
from app.config import Settings
from app.domain.models import EvalCase, TripContext
from app.eval_efficiency import build_rig, case_context
from app.eval_metering import RunLedger
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer
from tests.test_conversation_context import RecordingLLM, _gateway


def _model(settings: Settings, reply: dict, message: str = "Tokyo then Kyoto") -> TripContext:
    llm = RecordingLLM(replies={"understand": json.dumps(reply)})
    return asyncio.run(understand(message, TripContext(session_id="s"), _gateway(settings, llm)))


def _offline(settings: Settings, message: str) -> TripContext:
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    return asyncio.run(understand(message, TripContext(session_id="s"), gateway))


def test_two_cities_with_nights_are_stored_in_order(settings: Settings) -> None:
    context = _model(
        settings,
        {"destination": "Kyoto", "stops": [{"city": "Tokyo", "nights": 4}, {"city": "Kyoto", "nights": None}]},
    )

    assert [(stop.city, stop.nights) for stop in context.stops] == [("Tokyo", 4), ("Kyoto", None)]
    assert context.destination == "Tokyo"


def test_one_city_leaves_the_stops_empty(settings: Settings) -> None:
    one = _model(settings, {"destination": "Kyoto", "stops": []}, "A trip to Kyoto")
    lone = _model(settings, {"destination": "Kyoto", "stops": [{"city": "Kyoto", "nights": 3}]}, "A trip to Kyoto")

    assert one.stops == [] and one.destination == "Kyoto"
    assert lone.stops == [] and lone.destination == "Kyoto"


def test_nine_cities_are_not_stored_and_are_counted(settings: Settings) -> None:
    stops = [{"city": f"City {n}", "nights": 1} for n in range(9)]

    context = _model(settings, {"destination": "City 0", "stops": stops})

    assert context.stops == []
    assert context.route_overflow == 9


def test_the_model_reads_the_trip_as_one_way(settings: Settings) -> None:
    assert _model(settings, {"destination": "Kyoto", "one_way": True}).one_way is True
    assert _model(settings, {"destination": "Kyoto"}).one_way is False


def test_nights_in_the_message_fill_the_stops_with_no_model(settings: Settings) -> None:
    context = _offline(settings, "Tokyo 4 nights then Kyoto 3 nights")

    assert [(stop.city, stop.nights) for stop in context.stops] == [("Tokyo", 4), ("Kyoto", 3)]
    assert context.destination == "Tokyo" and context.days == 8


def test_three_cities_after_a_departure_city(settings: Settings) -> None:
    context = _offline(
        settings, "Plan a trip from Hong Kong to Tokyo 4 nights, Kyoto 3 nights and Osaka 2 nights for 2 people"
    )

    assert [(stop.city, stop.nights) for stop in context.stops] == [("Tokyo", 4), ("Kyoto", 3), ("Osaka", 2)]
    assert context.origin == "Hong Kong" and context.destination == "Tokyo"


def test_a_single_city_message_is_read_as_before(settings: Settings) -> None:
    context = _offline(settings, "Plan a 4-day Kyoto trip from Hong Kong for 2 in April. I want a slow pace.")

    assert context.stops == [] and context.destination == "Kyoto" and context.origin == "Hong Kong"
    assert context.days == 4 and "slow pace" in context.preferences


def test_a_message_the_pattern_does_not_match_changes_nothing(settings: Settings) -> None:
    context = _offline(settings, "I stayed 3 nights in Tokyo last year, now 5 nights in Kyoto please")

    assert context.stops == []


def test_a_turn_with_nine_cities_asks_which_to_keep(settings: Settings) -> None:
    settings = settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})
    stops = [{"city": f"City {n}", "nights": 1} for n in range(9)]

    async def main():
        ledger = RunLedger()
        try:
            rig = build_rig(settings, "Kyoto", ledger)
            rig.llm.replies["understand"] = json.dumps({"destination": "City 0", "stops": stops})
            rig.llm.route = {"intent": "plan", "confidence": 0.95}
            session = ledger.store.create_session("guest-stops")
            context = case_context(EvalCase(id="c", dataset="d", input=""), session.id)
            context.destination = ""
            ledger.store.save_context(context)
            events = [e async for e in rig.turns.handle_turn(session_id=session.id, message="nine cities please")]
            return events, ledger.store.get_context(session.id), ledger.store.get_plan(session.id)
        finally:
            ledger.close()

    events, saved, plan = asyncio.run(main())

    clarify = [e for e in events if e.event == "clarify"]
    assert clarify and "up to 8 cities" in clarify[0].data["question"]
    assert plan is None and saved.stops == []
