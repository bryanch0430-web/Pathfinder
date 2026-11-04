"""A length or date statement in plain words, on a trip that has a plan, edits that plan; it does not start a new trip.

Before, "only 2 days", "4 nights please" or "let's do 4 days" had a day count and no change verb, so the rules read a
planning request with a concrete detail and decided a NEW TRIP alone: the locked stops and the accepted hotel were
dropped and the length asked for was not applied either. A new trip needs a destination or words that say so.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.router import RuleRouter, System1Router, asks_for_new_dates, rules_first_applies
from app.config import Settings
from app.domain.models import Intent, TripContext
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer

START = date.today() + timedelta(days=30)
PLAN = {
    "trip_id": "t",
    "destination": "Kyoto",
    "dates": [START.isoformat(), (START + timedelta(days=2)).isoformat()],
    "days": [{"items": [{"name": "Gion"}, {"name": "Kiyomizu-dera"}]}],
}
FORM = TripContext(
    session_id="s",
    destination="Kyoto",
    origin="Hong Kong",
    start_date=START,
    end_date=START + timedelta(days=2),
    party_size=2,
    budget=400000,
    currency="JPY",
)

#: a length or a date range said in plain words: each one asks for another shape of the trip that exists
LENGTH_STATEMENTS = [
    "5 days instead of 3",
    "4 nights please",
    "only 2 days",
    "I only have 2 days now",
    "let's do 4 days",
    "reduce it to 3 days",
    "trim a day off the trip",
    "the trip should start on the 20th",
    "the trip should be 6 days",
    "make it a weekend trip",
    "make it a long weekend",
    "3 nights instead",
    "keep it to 2 days",
    "let's stay 4 nights",
    "we want 5 days",
    "3 days is enough",
    "make that 4 days",
    "I want 6 days in total",
    "2 days only",
    "I'd like a 2 day trip instead",
    "a 3 night stay would be better",
    "we only need 3 nights",
    "end the trip on day 2",
    "stop after day 2",
    "travel the week after",
    "trip dates 3 to 5 May",
    "can we do 3 days instead",
    "can it be 4 days",
    "can we stay a few more days",
    "make the trip longer",
    "make the trip shorter",
    "add one night",
    "remove a night",
    "take off one day",
    "two more days",
    "change to next month",
]

#: sentences with a day count, a night or a date in them that are not a request for another length
NOT_A_LENGTH_STATEMENT = [
    "Plan a 5-day trip to Kyoto for 2 people in April, budget 4000 USD",
    "I want a 4 day trip to Osaka",
    "plan a new trip to Tokyo for 5 days",
    "Going to Tokyo for 10 days in spring with my wife, we love art and ramen",
    "Is 2 days enough for Kyoto?",
    "What can we do in 2 days?",
    "How many nights do I stay?",
    "add a day trip to Nara",
    "add one more day trip to Nara",
    "add a day at the beach",
    "the trip should be cheaper",
    "start the trip at Fushimi Inari",
    "I want to stay 2 nights at a ryokan",
    "Add a dim sum lunch on day 2",
    "Cancel the day trip to Nara and stay in Osaka instead",
]


@pytest.fixture()
def router(settings: Settings) -> System1Router:
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    return System1Router(gateway=gateway, settings=settings, use_model=True)


@pytest.mark.parametrize("message", LENGTH_STATEMENTS)
def test_a_length_statement_in_plain_words_asks_for_other_dates(message: str) -> None:
    assert asks_for_new_dates(message) is True


@pytest.mark.parametrize("message", NOT_A_LENGTH_STATEMENT)
def test_a_sentence_that_is_not_a_length_statement_asks_for_no_other_dates(message: str) -> None:
    assert asks_for_new_dates(message) is False


@pytest.mark.parametrize("message", LENGTH_STATEMENTS)
def test_the_rules_alone_make_a_length_statement_an_edit_of_the_dates(router: System1Router, message: str) -> None:
    """Whatever the model would say, the rules' own decision is an edit, so with no model it still is one."""
    decision = asyncio.run(router.route(message=message, context=FORM, plan_summary=PLAN, history=[]))

    assert decision.intent is Intent.MODIFY, decision
    assert "dates" in decision.affected_parts, decision
    assert not decision.need_clarification


@pytest.mark.parametrize("message", LENGTH_STATEMENTS)
def test_a_length_statement_without_a_plan_is_not_an_edit(router: System1Router, message: str) -> None:
    decision = asyncio.run(router.route(message=message, context=FORM, plan_summary=None, history=[]))

    assert decision.intent is not Intent.MODIFY


@pytest.mark.parametrize("message", ["make this trip cheaper", "make the trip a bit shorter", "make my trip longer"])
def test_making_the_trip_cheaper_or_longer_is_not_asking_for_a_new_trip(message: str) -> None:
    decision = RuleRouter().route(message=message, context=FORM, plan_summary=PLAN, history=[])

    assert decision.intent is Intent.MODIFY, decision


@pytest.mark.parametrize(
    "message",
    ["Plan a 5-day trip to Osaka", "make a trip to Rome", "create a new trip for 4 days", "plan another trip to Kyoto"],
)
def test_a_new_trip_in_words_is_still_a_new_trip_on_a_session_with_a_plan(message: str) -> None:
    decision = RuleRouter().route(message=message, context=FORM, plan_summary=PLAN, history=[])

    assert decision.intent is Intent.PLAN, decision


def test_no_length_statement_is_left_to_a_new_trip_decided_by_the_rules_alone(settings: Settings) -> None:
    for message in LENGTH_STATEMENTS:
        ruled = RuleRouter().route(message=message, context=FORM, plan_summary=PLAN, history=[])
        alone = rules_first_applies(message, ruled, context=FORM, plan_summary=PLAN, settings=settings)

        assert not (alone and ruled.intent is Intent.PLAN), message


PACE_WORDS = [
    "make the trip slower",
    "make the plan lighter",
    "make this trip easier",
    "make our holiday calmer",
    "make the trip a bit quieter",
    "make my trip faster",
]


@pytest.mark.parametrize("message", PACE_WORDS)
def test_making_the_trip_slower_or_lighter_is_not_read_as_an_edit_that_names_no_stop(message: str) -> None:
    """The edit path cannot apply a pace word to the trip; routed there it answered "I could not tell which stop you meant"."""
    decision = RuleRouter().route(message=message, context=FORM, plan_summary=PLAN, history=[])

    assert decision.intent is not Intent.MODIFY, decision


@pytest.mark.parametrize("message", PACE_WORDS)
def test_a_turn_that_asks_for_a_slower_trip_does_not_answer_with_a_stop_it_could_not_find(
    settings: Settings, message: str
) -> None:
    from tests.test_modify_dates import Trip, reply_of, run_trip

    async def scenario(trip: Trip) -> None:
        events = await trip.turn(message)

        assert "could not tell which stop" not in reply_of(events), reply_of(events)
        assert trip.store.get_plan(trip.session_id).version == 1

    run_trip(settings, scenario)
