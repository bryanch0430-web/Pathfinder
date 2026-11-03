"""A request to move the trip or change its length reaches the edit path, in whatever words it comes.

Before, `shift the trip by a week` was read as a new trip (the word "trip"), `two days later` and `postpone it by
two weeks` as unclear, `can we go a week earlier?` as a question: a new trip drops the locked stops and the hotel the
traveller accepted. A day, a night or "later" on its own still names a part of the trip and is not a new date.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.router import System1Router
from app.config import Settings
from app.domain.models import Intent, TripContext
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer

START = date.today() + timedelta(days=30)
#: the plan has its dates; the form already holds the dates the traveller wants
PLAN = {"trip_id": "t", "destination": "Kyoto", "dates": [START.isoformat(), (START + timedelta(days=2)).isoformat()]}
FORM = TripContext(
    session_id="s",
    destination="Kyoto",
    origin="Hong Kong",
    start_date=START + timedelta(days=7),
    end_date=START + timedelta(days=9),
    party_size=2,
    budget=400000,
    currency="JPY",
)

#: ways to ask for other dates or another length; every one of them is a change to the plan that exists
DATE_REQUESTS = [
    "move the trip a week later",
    "move the trip to 12 to 14 December",
    "move it to 12-14 December",
    "move my trip to next month",
    "make it 4 days",
    "change it to 5 days",
    "cut it down to 2 days",
    "turn it into a 3 day trip",
    "move the trip 3 days earlier",
    "change the dates",
    "change the dates to 5-7 January",
    "update the dates to match my new leave",
    "same trip, same hotel, new dates",
    "move the dates but keep the same room",
    "extend the trip by two days",
    "extend my stay by two days",
    "shorten it by a day",
    "shift the trip by a week",
    "shift everything one day later",
    "shift my stay by a day",
    "push the whole trip back a week",
    "postpone it by two weeks",
    "reschedule the trip for the following weekend",
    "two days later",
    "can we go a week earlier?",
    "keep the hotel but move to March",
    "make it cheaper and move it to May",
    "I'd like to travel in March instead",
    "stay at the same hotel but go on 20 March instead",
    "keep The Kyoto Grand but move the trip a week later",
]

#: a day, a night, a time or a hotel's nights on their own: parts of the trip, not other dates
PARTS_OF_THE_TRIP = [
    "add a temple on day 2",
    "remove Gion from day 3",
    "swap the hotel for the last two nights",
    "change the flight to a later one",
    "move Gion later in the afternoon",
    "move lunch earlier",
    "what dates does the museum open?",
    "make it 3 places a day",
    "make it 2 hours",
    "extend lunch by half an hour",
    "put the museum on day 2",
    "I want to go to Osaka in March",
    "Move the dinner to 8pm",
    "add a night market on day 2",
    "make it a night to remember",
    "a later flight please",
]


@pytest.fixture()
def router(settings: Settings) -> System1Router:
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    return System1Router(gateway=gateway, settings=settings, use_model=True)


@pytest.mark.parametrize("message", DATE_REQUESTS)
def test_a_request_for_other_dates_is_an_edit_of_the_plan(router: System1Router, message: str) -> None:
    decision = asyncio.run(router.route(message=message, context=FORM, plan_summary=PLAN, history=[]))

    assert decision.intent is Intent.MODIFY, decision
    assert "dates" in decision.affected_parts
    assert not decision.need_clarification


@pytest.mark.parametrize("message", DATE_REQUESTS)
def test_a_request_for_other_dates_names_no_new_trip_without_a_plan(router: System1Router, message: str) -> None:
    """Nothing to move yet: it is never an edit, so nothing is invented for it."""
    decision = asyncio.run(router.route(message=message, context=FORM, plan_summary=None, history=[]))

    assert decision.intent is not Intent.MODIFY


@pytest.mark.parametrize("message", PARTS_OF_THE_TRIP)
def test_a_part_of_the_trip_is_not_a_request_for_other_dates(message: str) -> None:
    from app.application.router import asks_for_new_dates

    assert asks_for_new_dates(message) is False
