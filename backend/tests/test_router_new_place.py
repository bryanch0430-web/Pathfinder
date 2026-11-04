"""A length said in plain words is a new trip when the sentence names another place than the plan's.

"I want 4 nights in Osaka" has a length and no change verb, like "I want 4 nights". With a Kyoto plan open, the second
edits the plan's dates; the first asks for a trip to Osaka. The turn reads the sentence into the trip details before it
is routed, so the details name Osaka by then.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.router import (
    System1Router,
    asks_for_other_dates,
    names_another_place,
    replans_new_destination,
    same_destination,
)
from app.application.understanding import understand
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


def _form(destination: str = "Kyoto") -> TripContext:
    return TripContext(
        session_id="s",
        destination=destination,
        origin="Hong Kong",
        start_date=START,
        end_date=START + timedelta(days=2),
        party_size=2,
        budget=400000,
        currency="JPY",
    )


def _route(settings: Settings, message: str, *, read_first: bool = True):
    """What the turn does: the sentence is read into the trip details, then routed with the plan in hand."""
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    router = System1Router(gateway=gateway, settings=settings, decision_client=None, use_model=True)

    async def run():
        context = await understand(message, _form(), gateway) if read_first else _form()
        return context, await router.route(message=message, context=context, plan_summary=PLAN, history=[])

    return asyncio.run(run())


@pytest.mark.parametrize(
    ("message", "place"),
    [
        ("I want 4 nights in Osaka", "Osaka"),
        ("let's do 5 days in Tokyo instead", "Tokyo"),
        ("We have 6 days, go to Seoul", "Seoul"),
        ("I'd like 5 nights in Paris", "Paris"),
        ("Tokyo for only 3 nights", "Tokyo"),
        ("we want 5 days in Taipei", "Taipei"),
    ],
)
def test_a_length_for_another_place_is_a_new_trip(settings: Settings, message: str, place: str) -> None:
    context, decision = _route(settings, message)

    assert context.destination == place
    assert decision.intent == Intent.PLAN, (message, decision.reason_code)


@pytest.mark.parametrize(
    "message",
    [
        "only 2 days in Kyoto",
        "I want 4 nights in Kyoto",
        "let's do 4 days",
        "I only have 2 days now",
        "4 nights please",
        "we want 5 days",
    ],
)
def test_a_length_for_the_place_of_the_plan_or_for_no_place_still_edits_the_plan(
    settings: Settings, message: str
) -> None:
    _, decision = _route(settings, message)

    assert decision.intent == Intent.MODIFY, (message, decision.reason_code)
    assert "dates" in decision.affected_parts


@pytest.mark.parametrize("message", ["make it 4 days", "shift the trip by a week", "add two more days", "extend my stay"])
def test_a_sentence_that_changes_the_trip_edits_it_whatever_the_form_says(settings: Settings, message: str) -> None:
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    router = System1Router(gateway=gateway, settings=settings, decision_client=None, use_model=True)
    stale = _form("Osaka")  # the details were changed to another place and not planned yet

    decision = asyncio.run(router.route(message=message, context=stale, plan_summary=PLAN, history=[]))

    assert decision.intent == Intent.MODIFY, (message, decision.reason_code)


def test_the_form_naming_another_place_is_what_makes_a_bare_length_a_new_trip() -> None:
    assert asks_for_other_dates("only 2 days", _form("Kyoto"), PLAN) is True
    assert asks_for_other_dates("only 2 days", _form("Osaka"), PLAN) is False
    assert asks_for_other_dates("make it 2 days", _form("Osaka"), PLAN) is True
    assert asks_for_other_dates("only 2 days", _form("Osaka"), None) is True  # no plan: nothing says it is a new trip


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        ("Kyoto", "Kyoto", True),
        ("Kyoto", "kyoto ", True),
        ("Kyoto", "Kyoto, Japan", True),
        ("Kyoto, Japan", "Kyoto", True),
        ("Hong  Kong", "hong kong", True),
        ("Kyoto", "Osaka", False),
        ("Kyoto", "", False),
        ("", "", False),
    ],
)
def test_two_spellings_of_one_destination(first: str, second: str, same: bool) -> None:
    assert same_destination(first, second) is same


@pytest.mark.parametrize(
    ("first", "second", "same"),
    [
        # one name is the start of the other: the same place, spelled longer
        ("Kyoto", "Kyoto Japan", True),
        ("Kyoto Japan", "Kyoto", True),
        ("Kyoto, Japan", "Kyoto Japan", True),
        ("New York", "New York City", True),
        ("Paris", "Paris, France", True),
        # both go on, differently: two places that share a first part
        ("Paris, France", "Paris, Texas", False),
        ("Portland, Oregon", "Portland, Maine", False),
        ("Portland, OR, USA", "Portland, Maine", False),
        ("New York", "New Yorkshire", False),
        ("San Jose, California", "San Jose, Costa Rica", False),
        ("Kyoto, Osaka", "Osaka", False),
    ],
)
def test_names_that_share_a_first_part_but_go_on_differently_are_two_places(first: str, second: str, same: bool) -> None:
    assert same_destination(first, second) is same
    assert same_destination(second, first) is same


def test_the_page_generate_again_message_makes_a_trip_to_the_other_region_a_new_trip() -> None:
    message = "Re-plan with the updated details."
    plan = {**PLAN, "destination": "Portland, Oregon"}

    assert replans_new_destination(message, _form("Portland, Maine"), plan) is True
    assert replans_new_destination(message, _form("Portland, Oregon"), plan) is False
    assert replans_new_destination(message, _form("Portland"), plan) is False


def test_the_page_generate_again_message_keeps_the_trip_when_the_form_spells_the_place_longer() -> None:
    message = "Re-plan with the updated details."

    assert replans_new_destination(message, _form("Kyoto, Japan"), PLAN) is False
    assert replans_new_destination(message, _form("Osaka"), PLAN) is True
    assert names_another_place(_form("Osaka"), PLAN) is True
    assert names_another_place(_form("Osaka"), None) is False
