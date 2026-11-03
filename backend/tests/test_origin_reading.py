"""A departure city is read only from a phrase that says it is one.

"From scratch", "from then on", "from the beginning" and "from the list above" are ways to say
something else, and a word after "from" is not a place because it follows "from". The departure city
is the one a sentence names with a travel word (flying, leaving, departing, travelling from ...), as
the start of a trip ("a 5-day trip from Leeds to Paris"), or as a city the assistant knows when the
trip has none yet. A phrase that is none of these never replaces the departure city a trip has.
"""

from __future__ import annotations

import asyncio
from datetime import date

import pytest

from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.understanding import _apply_rules, understand
from app.config import Settings
from app.domain.models import TripContext
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer

TODAY = date(2026, 11, 3)

#: Sentences in which "from <words>" is no departure city.
NOT_A_DEPARTURE = [
    "Start the plan again from scratch",
    "Plan it from scratch",
    "Start over from scratch.",
    "From now on only recommend ramen",
    "From then on only recommend ramen",
    "from then on no museums",
    "From here on keep it cheap",
    "From this point on, no museums",
    "redo it from the beginning",
    "Redo the plan from the beginning",
    "Change it from the start",
    "Replace the hotel from the list above",
    "change it from my last plan",
    "keep the budget from the earlier plan",
    "make day 2 cheaper from lunch onwards",
    "from time to time add a cafe",
    "from there we take the train",
    "from the airport go to the hotel",
    "Move it from Friday to Saturday",
    "From tomorrow keep it light",
    "From next week please add more food stops",
    "swap the hotel from Kyoto to Osaka",
    "Change the trip from Kyoto to Osaka",
]

#: A known city after "from" can fill a departure city that is missing, but a sentence that is not
#: about leaving never replaces the one a trip has.
NOT_REPLACING = ["show me restaurants from Osaka", "Plan Kyoto from Singapore"]

#: Sentences that name the departure city, with the city they name.
A_DEPARTURE = [
    ("Plan a 4-day Kyoto trip from Hong Kong for 2 in April", "Hong Kong"),
    ("Plan a 2-day trip to Shanghai from Hong Kong for 1 person.", "Hong Kong"),
    ("Plan a 3-day trip to Hong Kong from Singapore for 2 people.", "Singapore"),
    ("I am flying from Hong Kong next week.", "Hong Kong"),
    ("Plan a 6-day trip to Korea from Hong Kong, 18 to 23 November 2026, 2 people", "Hong Kong"),
    ("Plan a trip to Kyoto, flying out from Osaka", "Osaka"),
    ("I live in Tampa, flights from Tampa to Kyoto please", "Tampa"),
    ("We are leaving from Heathrow on 3 December", "Heathrow"),
    ("departing from Manchester", "Manchester"),
    ("a 5-day trip from Leeds to Paris", "Leeds"),
    ("I'm travelling from Edinburgh to Paris for 5 days", "Edinburgh"),
    ("coming from Zurich", "Zurich"),
    ("flights from HKG to KIX", "HKG"),
    ("Move it from Friday to Saturday, I am flying from Osaka", "Osaka"),
    ("Plan Kyoto from Hong Kong", "Hong Kong"),
]


def _gateway(settings: Settings) -> LLMGateway:
    return LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)


@pytest.mark.parametrize("text", NOT_A_DEPARTURE)
def test_a_phrase_that_is_not_a_departure_city_names_no_origin(text: str) -> None:
    context = TripContext(session_id="s")

    _apply_rules(text, context, TODAY)

    assert context.origin == ""


@pytest.mark.parametrize("text", NOT_A_DEPARTURE + NOT_REPLACING)
def test_such_a_phrase_leaves_the_departure_city_a_trip_has(settings: Settings, text: str) -> None:
    stored = TripContext(
        session_id="s", destination="Kyoto", origin="Hong Kong", start_date=date(2026, 12, 3), end_date=date(2026, 12, 5),
        party_size=2, budget=4000,
    )

    updated = asyncio.run(understand(text, stored, _gateway(settings), today=TODAY))

    assert updated.origin == "Hong Kong"


@pytest.mark.parametrize(("text", "city"), A_DEPARTURE)
def test_a_sentence_that_names_the_departure_city_still_sets_it(text: str, city: str) -> None:
    context = TripContext(session_id="s")

    _apply_rules(text, context, TODAY)

    assert context.origin == city


def test_a_new_request_from_another_city_replaces_the_departure_city() -> None:
    context = TripContext(session_id="s", destination="Kyoto", origin="Singapore")

    _apply_rules("Plan a 4-day Kyoto trip from Hong Kong for 2 in April", context, TODAY)
    assert context.origin == "Hong Kong"

    _apply_rules("Actually I'll be flying from Osaka", context, TODAY)
    assert context.origin == "Osaka"


def test_a_known_city_after_from_fills_a_missing_departure_city_but_never_replaces_one() -> None:
    empty = TripContext(session_id="s")
    stored = TripContext(session_id="s", origin="Hong Kong")

    _apply_rules("Plan Kyoto from Singapore", empty, TODAY)
    _apply_rules("Plan Kyoto from Singapore", stored, TODAY)

    assert empty.origin == "Singapore"
    assert stored.origin == "Hong Kong"


def test_the_destination_is_still_found_when_the_from_phrase_names_no_departure() -> None:
    context = TripContext(session_id="s")

    _apply_rules("Plan 3 days in Kyoto, I will redo the list from scratch", context, TODAY)

    assert context.destination == "Kyoto" and context.origin == ""
