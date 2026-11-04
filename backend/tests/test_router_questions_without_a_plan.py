"""A question that carries a length is still a question when there is no plan to change.

"can I do 4 days in Kyoto?" holds a length said in plain words ("I do 4 days"). With a plan open that is a request for
other dates, and the turn answers a question about the selected stop as a question anyway. With no plan there is
nothing to move, so the rules read the sentence as before: a question is an ask, and "we need 4 days" plans a trip.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.router import RuleRouter, System1Router, rules_decide_alone
from app.application.understanding import understand
from app.config import Settings
from app.domain.models import Intent, TripContext
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer

START = date.today() + timedelta(days=30)


def _form(destination: str = "Kyoto") -> TripContext:
    return TripContext(
        session_id="s",
        destination=destination,
        origin="Hong Kong",
        start_date=START,
        end_date=START + timedelta(days=2),
        days=3,
        party_size=2,
        budget=400000,
        currency="JPY",
    )


def _route(settings: Settings, message: str, *, context: TripContext | None = None):
    """What the turn does with no plan: the sentence is read into the trip details, then routed."""
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    router = System1Router(gateway=gateway, settings=settings, decision_client=None, use_model=True)

    async def run():
        read = await understand(message, context or _form(), gateway)
        return await router.route(message=message, context=read, plan_summary=None, history=[])

    return asyncio.run(run())


QUESTIONS_WITH_A_LENGTH = [
    "can I do 4 days in Kyoto?",
    "can we do 4 days?",
    "do we need 4 days?",
    "why only 2 days?",
    "should I stay 4 nights in Osaka?",
    "could we only do 3 days?",
    "would 3 days be enough?",
]


@pytest.mark.parametrize("message", QUESTIONS_WITH_A_LENGTH)
def test_a_question_that_carries_a_length_is_an_ask_when_there_is_no_plan(settings: Settings, message: str) -> None:
    decision = _route(settings, message)

    assert decision.intent == Intent.ASK, (message, decision.reason_code)
    assert decision.reason_code == "question"


@pytest.mark.parametrize("message", QUESTIONS_WITH_A_LENGTH)
def test_the_same_question_with_no_trip_details_is_not_a_planning_request_either(settings: Settings, message: str) -> None:
    decision = _route(settings, message, context=TripContext(session_id="s"))

    assert decision.reason_code != "new_trip_request", (message, decision.intent)
    assert not decision.reason_code.startswith("missing_"), (message, decision.reason_code)


@pytest.mark.parametrize("message", ["we need 4 days", "let's do 4 days", "I want 4 nights in Osaka", "only 2 days"])
def test_a_length_said_in_plain_words_plans_a_trip_when_there_is_no_plan(settings: Settings, message: str) -> None:
    decision = _route(settings, message)

    assert decision.intent == Intent.PLAN, (message, decision.reason_code)


@pytest.mark.parametrize("message", ["can you make it 4 days?", "could you shift the trip by a week?", "can you extend my stay?"])
def test_a_question_with_a_change_of_dates_in_it_is_still_not_an_ask(settings: Settings, message: str) -> None:
    """The sentences that move or shorten a trip count as changes with no plan too, as they always did."""
    decision = RuleRouter().route(message=message, context=_form(), plan_summary=None, history=[])

    assert decision.intent != Intent.ASK, (message, decision.reason_code)


def test_the_rules_decide_a_question_with_a_length_in_words_alone_when_there_is_no_plan() -> None:
    message = "can we do four days in Kyoto?"
    decision = RuleRouter().route(message=message, context=_form(), plan_summary=None, history=[])

    assert decision.intent == Intent.ASK
    assert rules_decide_alone(message, decision, context=_form(), plan_summary=None) is True
