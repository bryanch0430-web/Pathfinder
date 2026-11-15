"""A trip of more than 8 cities or 30 days is asked about before anything runs, not refused while planning."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from multi_city_rig import START, multi_city_rig, route_context

from app.config import Settings
from app.domain.models import EvalCase, TripContext
from app.eval_efficiency import case_context
from app.eval_metering import RunLedger

CITIES = [
    "Tokyo",
    "Kyoto",
    "Osaka",
    "Nara",
    "Kobe",
    "Himeji",
    "Nagoya",
    "Kanazawa",
    "Sendai",
]


@dataclass
class Turn:
    events: list
    context: TripContext
    plan: Any
    tool_calls: int

    @property
    def clarifies(self) -> list:
        return [event for event in self.events if event.event == "clarify"]

    @property
    def errors(self) -> list:
        return [event for event in self.events if event.event == "error"]


def _turn(
    settings: Settings,
    *,
    context: TripContext | None = None,
    understood: dict | None = None,
    message: str = "Plan this trip",
) -> Turn:
    """One planning turn on the offline rig: on a stored form, or on an empty trip whose message the model reads as
    ``understood``."""
    settings = settings.model_copy(
        update={
            "preplan_budget_seconds": 15,
            "agent_timeout_seconds": 8,
            "enable_flights": True,
        }
    )

    async def main() -> Turn:
        ledger = RunLedger()
        try:
            rig = multi_city_rig(settings, ledger)
            rig.llm.route = {"intent": "plan", "confidence": 0.95}
            session = ledger.store.create_session("guest-too-large")
            if understood is not None:
                rig.llm.replies["understand"] = json.dumps(understood)
                form = case_context(EvalCase(id="c", dataset="d", input=""), session.id)
                form.destination = ""
            else:
                form = context
                form.session_id = session.id
            ledger.store.save_context(form)
            events = [
                e
                async for e in rig.turns.handle_turn(
                    session_id=session.id, message=message
                )
            ]
            return Turn(
                events,
                ledger.store.get_context(session.id),
                ledger.store.get_plan(session.id),
                len(ledger.store.tool_calls()),
            )
        finally:
            ledger.close()

    return asyncio.run(main())


def test_nine_cities_get_one_question_and_no_plan(settings: Settings) -> None:
    stops = [{"city": city, "nights": 1} for city in CITIES]

    turn = _turn(
        settings,
        understood={"destination": CITIES[0], "stops": stops},
        message="nine cities please",
    )

    assert len(turn.clarifies) == 1 and not turn.errors
    question = turn.clarifies[0].data["question"]
    assert "8 cities" in question and "fewer" in question.lower()
    assert turn.plan is None and turn.context.stops == [] and turn.tool_calls == 0


def test_nine_cities_named_with_their_nights_get_one_question_and_no_plan(settings: Settings) -> None:
    message = " then ".join(f"{city} 1 night" for city in CITIES)

    turn = _turn(settings, understood={"destination": "Tokyo"}, message=message)

    assert len(turn.clarifies) == 1 and not turn.errors
    assert "8 cities" in turn.clarifies[0].data["question"]
    assert turn.plan is None and turn.context.stops == [] and turn.tool_calls == 0


def test_a_form_over_the_city_limit_setting_asks_and_plans_nothing(
    settings: Settings,
) -> None:
    few = settings.model_copy(update={"max_cities": 2})

    turn = _turn(
        few, context=route_context("s", [("Tokyo", 2), ("Kyoto", 2), ("Osaka", 2)])
    )

    assert len(turn.clarifies) == 1 and not turn.errors
    assert "2 cities" in turn.clarifies[0].data["question"]
    assert turn.plan is None and turn.tool_calls == 0


def test_forty_days_get_one_question_and_no_plan(settings: Settings) -> None:
    turn = _turn(settings, context=route_context("s", [("Kyoto", None)], nights=39))

    assert len(turn.clarifies) == 1 and not turn.errors
    question = turn.clarifies[0].data["question"]
    assert "30 days" in question and "40" in question
    assert "fewer days" in question.lower() and "fewer cities" in question.lower()
    assert turn.plan is None and turn.tool_calls == 0
    assert turn.context.days == 40


def test_forty_days_across_cities_ask_too(settings: Settings) -> None:
    turn = _turn(
        settings,
        context=route_context("s", [("Tokyo", 13), ("Kyoto", 13), ("Osaka", 13)]),
    )

    assert len(turn.clarifies) == 1 and not turn.errors
    assert "30 days" in turn.clarifies[0].data["question"]
    assert turn.plan is None and turn.tool_calls == 0


def test_forty_days_named_in_the_message_ask_and_write_nothing(
    settings: Settings,
) -> None:
    turn = _turn(
        settings,
        understood={
            "destination": "Kyoto",
            "origin": "Hong Kong",
            "days": 40,
            "start_date": "2027-03-01",
            "party_size": 2,
            "budget": 90000,
            "currency": "HKD",
        },
        message="Plan 40 days in Kyoto from Hong Kong for 2 starting 1 March 2027",
    )

    assert len(turn.clarifies) == 1 and not turn.errors
    assert "30 days" in turn.clarifies[0].data["question"]
    assert turn.plan is None and turn.tool_calls == 0


def test_the_question_for_days_offers_a_trip_of_the_limit(settings: Settings) -> None:
    turn = _turn(
        settings,
        context=route_context(
            "s", [("Kyoto", None)], start=date(2027, 3, 1), nights=39
        ),
    )

    data = turn.clarifies[0].data
    assert data["slots"] == ["dates"]
    assert data["options"]["dates"] == ["2027-03-01 to 2027-03-30"]


def test_a_trip_of_exactly_thirty_days_is_still_planned(settings: Settings) -> None:
    turn = _turn(settings, context=route_context("s", [("Kyoto", None)], nights=29))

    assert not turn.clarifies and not turn.errors
    assert turn.plan is not None and len(turn.plan.days) == 30


def test_eight_cities_over_thirty_days_are_still_planned(settings: Settings) -> None:
    stops = list(zip(CITIES[:8], [4, 4, 4, 4, 4, 3, 3, 3]))
    context = route_context("s", stops)
    assert context.end_date == START + timedelta(days=29)

    turn = _turn(settings, context=context)

    assert not turn.clarifies and not turn.errors
    assert (
        turn.plan is not None
        and len(turn.plan.days) == 30
        and len(turn.plan.segments) == 8
    )
