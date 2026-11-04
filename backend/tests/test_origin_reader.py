"""The departure city is read only from a sentence that says it is one.

A word after "from" is not a city because it follows "from": "from scratch", "from Friday to Sunday", "a day trip
from Kyoto to Nara", "the travel insurance from Allianz" and "how far is the temple from Kyoto Station?" say other
things, and a trip that has a departure city keeps it. The sentences below are the ones a second round of review
used to measure the reader (145 messages, each labelled with the city it names, if any); the departure city is
named after a travel verb directly before "from", by a stated origin, by a trip or a planning request, and it
ends where the name ends ("Chicago and staying in Rome" is Chicago). An explicit change replaces the city a trip
has; a phrase that merely mentions a city never does.
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
HK = "Hong Kong"

#: (sentence, the city it names on a trip that has none, the city on a trip that leaves from Hong Kong).
#: None: the sentence says nothing either way for that trip.
DEPARTURES = [
    ("Plan a 5 day trip to Kyoto from Hong Kong", "Hong Kong", "Hong Kong"),
    ("5 days in Tokyo, flying from London", "London", "London"),
    ("A week in Paris from New York", "New York", "Hong Kong"),
    ("Flights from Singapore to Bangkok next month", "Singapore", "Singapore"),
    ("I'm leaving from Manchester on the 3rd", "Manchester", "Manchester"),
    ("Trip from Leeds to Paris, 5 days", "Leeds", "Leeds"),
    ("Actually I'm flying from Osaka instead", "Osaka", "Osaka"),
    ("We're travelling from Mumbai to Kyoto in April", "Mumbai", "Mumbai"),
    ("departing from Chicago, 4 nights in Rome", "Chicago", "Chicago"),
    ("I'll fly from Boston to Tokyo", "Boston", "Boston"),
    ("Flight from Tokyo to Osaka", "Tokyo", None),
    ("We are flying from Sydney", "Sydney", "Sydney"),
    ("coming from Berlin", "Berlin", "Berlin"),
    ("from Hong Kong, 4 days in Osaka", "Hong Kong", "Hong Kong"),
    ("leaving from Dublin airport on Friday", "Dublin Airport", None),
    ("Flying from Seoul via Hong Kong to Rome", "Seoul", "Seoul"),
    ("Cheap flights from Taipei to Kyoto", "Taipei", "Taipei"),
    ("I live in Tampa, flights from Tampa to Kyoto please", "Tampa", "Tampa"),
    ("Trip to Rome from Toronto for two", "Toronto", "Toronto"),
    ("We depart from Denver", "Denver", "Denver"),
    ("I am flying from Chicago and staying in Rome", "Chicago", "Chicago"),
    ("Travelling from Boston and visiting Kyoto in March", "Boston", "Boston"),
    ("Leaving from Leeds around noon to Paris", "Leeds", "Leeds"),
    ("Flying from Leeds via Dubai to Tokyo", "Leeds", "Leeds"),
    ("Flying from Leeds at noon to Paris", "Leeds", "Leeds"),
    ("We fly from Phoenix by bus to Las Vegas", "Phoenix", "Phoenix"),
    ("We are travelling from Delhi and want 5 days in Kyoto", "Delhi", "Delhi"),
    ("travelling from Lagos with two kids to Kyoto", "Lagos", "Lagos"),
    ("Coming from Cairo, we want to see Kyoto", "Cairo", "Cairo"),
    ("Departing from Singapore Changi Airport to Tokyo", "Singapore", "Singapore"),
    ("Flying from San Francisco to Tokyo", "San Francisco", "San Francisco"),
    ("Flights from Hong Kong airport to Osaka", "Hong Kong", "Hong Kong"),
    ("Leave from Hong Kong International Airport on Dec 3", "Hong Kong", "Hong Kong"),
    ("fly from LHR to HKG", "LHR", "LHR"),
    ("Flights from JFK to KIX", "JFK", "JFK"),
    ("Plan Kyoto from Singapore", "Singapore", "Hong Kong"),
    ("Arriving from Hong Kong on 3 December", "Hong Kong", "Hong Kong"),
    ("returning from Tokyo to Hong Kong on 10 Dec", "Tokyo", None),
    ("Return flight from Tokyo to Hong Kong on 10 Dec", "Tokyo", None),
    ("We fly back from Osaka", "Osaka", None),
    ("Our flight back leaves from Narita", "Narita", None),
]

#: Sentences with a "from" phrase that names no departure city: neither a trip without one gets one from them, nor a trip with one loses it.
NOT_DEPARTURES = [
    "Start the plan again from scratch",
    "Redo it from scratch please",
    "From now on only recommend ramen",
    "From then on avoid museums",
    "redo it from the beginning",
    "How far is the hotel from the airport?",
    "How far is the temple from Kyoto Station?",
    "How far is Fushimi Inari from Kyoto Station?",
    "What's the distance from Tokyo to Kyoto?",
    "Can I take the train from Osaka to Kyoto?",
    "What's the best way to get from Kyoto Station to Arashiyama?",
    "How do I get from the airport to the hotel",
    "Is a taxi from Gion to Kiyomizu expensive?",
    "I want a hotel from 3pm",
    "Dinner from 7 to 9",
    "Check in from Monday to Wednesday",
    "I'm free from Friday to Sunday",
    "We are in Kyoto from Friday to Sunday",
    "Stay from December 3 to December 5",
    "Move the museum from day 2 to day 3",
    "Move the museum from Monday to Tuesday",
    "Swap the dinner on day 2 from Kyoto cuisine to sushi",
    "Replace the dinner from the Michelin list with ramen",
    "Pick restaurants from Michelin guide",
    "Suggest ideas from Lonely Planet",
    "Use the reviews from Google",
    "Book a taxi from the hotel to the airport",
    "What's the weather in Osaka from Friday to Sunday?",
    "Any festivals in Kyoto from Dec 3 to Dec 5?",
    "Show me restaurants from Osaka",
    "Add a day trip from Kyoto to Nara",
    "Add a half-day trip from Osaka",
    "Can we do a day trip from Tokyo to Hakone?",
    "Is a side trip from Kyoto to Himeji doable?",
    "What about a day trip to Nara from Kyoto?",
    "Plan a day trip from Kyoto to Nara on day 3",
    "Add a Nara day trip from Kyoto on day 2",
    "Swap the hotel for something closer to the station from the list above",
    "I need a flight from Tokyo to Osaka on day 3",
    "Is there a flight from Tokyo to Osaka on day 3?",
    "How long is the flight from Osaka to Tokyo?",
    "Travel time from Gion to Kiyomizu?",
    "What is the travel time from Tokyo to Kyoto by shinkansen?",
    "Is the travel insurance from Allianz worth it?",
    "Recommend a travel guide from Lonely Planet",
    "Redo my travel plans from scratch",
    "Rebuild the whole travel itinerary from scratch",
    "Our travel dates are from Friday to Sunday",
    "We are travelling from Monday to Friday",
    "Flying from Friday to Sunday please",
    "Travel window is from Saturday to Tuesday",
    "Departing from work on Friday",
    "Traveling from the UK to Japan",
    "flying from my place in Leeds",
    "Flights from budget airlines only",
    "Flights from low-cost carriers",
    "Show flights from Japan Airlines",
    "departing from terminal two",
    "Show me Kyoto temples from the Edo period",
    "Show me Nara deer facts from Wikipedia",
    "Add ramen places from Fukuoka style",
    "Add Hakata ramen from Fukuoka",
    "Souvenirs from Kyoto for my mom",
    "Gifts from Osaka to bring home",
    "What is the best souvenir from Tokyo?",
    "Is it far from Tokyo?",
    "Is the hotel far from Shibuya?",
    "Is the hotel far from Shibuya Station?",
    "Is the ryokan far from Kyoto Station?",
    "How long to walk from Kyoto Station to the hotel?",
    "Hotel within 10 minutes from Tokyo Station",
    "Hotel near the airport from Hong Kong",
    "A hotel not far from Osaka Castle",
    "Best view from Tokyo Tower",
    "Tickets from Universal Studios Japan",
    "The cheapest ticket from Kyoto Station to Nara?",
    "Convert 5000 yen from Tokyo prices to USD",
    "Compare prices from Osaka and Kyoto",
    "Tell me about the food from Osaka",
    "Return from Osaka on day 5",
    "Come back from Osaka by train",
    "Arrive from the airport by 3pm",
    "Pick up from Kyoto Station at 9",
    "The shuttle from Narita airport takes 90 minutes",
    "The shuttle from Narita to Tokyo",
    "The limousine bus from Haneda to Shibuya",
    "Bus from Shinjuku to Hakone",
    "Train from Kyoto to Osaka",
    "Drive from Kyoto to Nara",
    "Walking from Gion to Kiyomizu-dera",
    "Transfers from Tokyo Station to Asakusa",
    "Travel from Kyoto Station to Nara by JR",
    "How do I travel from Osaka to Kyoto?",
    "Travelling around from Osaka to Nara and Kyoto",
    "We want to move on from Kyoto to Osaka on day 3",
    "leave Kyoto from the station",
    "After we leave from the Kyoto hotel",
    "We arrive in Kyoto from Osaka on day 1",
]

#: A place that is home or work is not a city.
NO_CITY = ["Leaving from home on Friday", "I'll be flying from home"]


def _gateway(settings: Settings) -> LLMGateway:
    return LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)


def _origin(text: str, stored: str = "", destination: str = "Kyoto") -> str:
    context = TripContext(session_id="s", destination=destination, origin=stored)
    _apply_rules(text, context, TODAY)
    return context.origin


@pytest.mark.parametrize(("text", "city", "_stored"), DEPARTURES, ids=[row[0] for row in DEPARTURES])
def test_a_sentence_that_states_a_departure_names_it_on_a_trip_that_has_none(text: str, city: str, _stored) -> None:
    assert _origin(text) == city


@pytest.mark.parametrize(
    ("text", "_city", "kept"), [row for row in DEPARTURES if row[2]], ids=[row[0] for row in DEPARTURES if row[2]]
)
def test_a_sentence_that_states_a_departure_is_read_the_same_on_a_trip_that_leaves_from_hong_kong(
    text: str, _city: str, kept: str
) -> None:
    assert _origin(text, stored=HK) == kept


@pytest.mark.parametrize("text", NOT_DEPARTURES)
def test_a_from_phrase_that_names_no_departure_gives_a_trip_none(text: str) -> None:
    assert _origin(text) == ""


@pytest.mark.parametrize("text", NOT_DEPARTURES)
def test_a_from_phrase_that_names_no_departure_leaves_the_departure_a_trip_has(text: str) -> None:
    assert _origin(text, stored=HK) == HK


@pytest.mark.parametrize("text", NO_CITY)
def test_home_is_not_a_departure_city(text: str) -> None:
    assert _origin(text) == "" and _origin(text, stored=HK) == HK


@pytest.mark.parametrize(
    ("text", "city"),
    [
        ("I am flying from Chicago and staying in Rome", "Chicago"),
        ("Travelling from Boston and visiting Kyoto in March", "Boston"),
        ("Flying from Leeds via Dubai to Tokyo", "Leeds"),
        ("Flying from Leeds at noon to Paris", "Leeds"),
        ("Leaving from Leeds around noon to Paris", "Leeds"),
        ("We fly from Phoenix by bus to Las Vegas", "Phoenix"),
        ("we are travelling from Delhi and want 5 days in Kyoto", "Delhi"),
        ("flying from Salt Lake City then driving to Kyoto", "Salt Lake City"),
        ("leaving from Leeds, then Paris", "Leeds"),
        ("departing from Leeds because it is cheaper", "Leeds"),
    ],
)
def test_the_name_of_an_unknown_city_ends_where_the_city_ends(text: str, city: str) -> None:
    assert _origin(text) == city and _origin(text, stored=HK) == city


@pytest.mark.parametrize("text", ["flying from Montr\u00e9al to Paris", "flying from Krak\u00f3w to Rome", "flying from Z\u00fcrich to Kyoto"])
def test_a_name_with_an_accent_is_not_cut_in_the_middle(text: str) -> None:
    """Names with accents are not read, but they are not read as the part of them before the accent either."""
    assert _origin(text) == "" and _origin(text, stored=HK) == HK


@pytest.mark.parametrize(
    "text",
    [
        # The words after the name are long enough for the part of them that is read to stop in the middle of a word.
        "I am flying from Leeds and staying in Rome for a very longer visit with my whole family",
        "I am flying from Leeds and staying in Rome for a very lengthy visit with my whole family",
        "I am flying from Leeds and staying in Rome for a wonderful visit with my whole family",
    ],
)
def test_a_long_sentence_after_the_name_does_not_hide_it(text: str) -> None:
    assert _origin(text) == "Leeds" and _origin(text, stored=HK) == "Leeds"


def test_a_name_that_runs_on_is_no_city() -> None:
    assert _origin("flying from the moon and back to Kyoto") == ""
    assert _origin("flying from one end of the country to the other") == ""
    assert _origin("flying from Leeds Bradford Central Valley Regional Airport Terminal") == ""


@pytest.mark.parametrize(
    "text",
    [
        "How far is the temple from Kyoto Station?",
        "How far is Fushimi Inari from Kyoto Station?",
        "What's the distance from Tokyo to Kyoto?",
        "Can I take the train from Osaka to Kyoto?",
        "Is the hotel far from Shibuya Station?",
        "Souvenirs from Kyoto for my mom",
        "Best view from Tokyo Tower",
        "Add a day trip from Kyoto to Nara",
        "Is a side trip from Kyoto to Himeji doable?",
        "How do I travel from Osaka to Kyoto?",
        "Is there a flight from Tokyo to Osaka on day 3?",
    ],
)
def test_a_question_or_a_place_that_is_not_the_city_does_not_fill_a_missing_departure_city(text: str) -> None:
    """The traveller is asked for the departure city, as the README says, instead of being given the nearest city named."""
    stored = TripContext(session_id="s", destination="Kyoto")

    updated = asyncio.run(understand(text, stored, _gateway_for_tests(), today=TODAY))

    assert updated.origin == "" and "origin" in updated.missing_slots()


@pytest.mark.parametrize(
    "text",
    [
        "Actually, plan it from Singapore",
        "Start from Singapore instead",
        "Make it from Singapore instead",
        "Plan Kyoto from Singapore instead",
        "Change the origin to Singapore",
        "Set my departure city to Singapore",
        "Use Singapore as the departure city",
        "Use Singapore as my origin",
        "My departure city is Singapore",
        "The origin is Singapore now",
        "Departure city: Singapore",
        "Fly from Singapore instead",
        "Leave from Singapore instead",
        "Depart from Singapore",
        "We now fly from Singapore",
    ],
)
def test_an_explicit_change_replaces_the_departure_city_a_trip_has(text: str) -> None:
    assert _origin(text, stored=HK) == "Singapore"


@pytest.mark.parametrize(
    "text",
    [
        "Swap the hotel from Kyoto to Osaka instead",
        "Show me restaurants from Osaka instead",
        "Move the museum from Monday to Tuesday instead",
        "Replace the dinner from the Michelin list with ramen instead",
        "Actually, add a day trip from Kyoto to Nara",
        "Actually, how far is the temple from Kyoto Station?",
        "Actually, make the dinner from Osaka cheaper",
        "Instead of that, pick a hotel from Osaka",
    ],
)
def test_the_word_instead_does_not_turn_an_edit_into_a_new_departure_city(text: str) -> None:
    assert _origin(text, stored=HK) == HK
    assert _origin(text) == ""


def test_a_stated_origin_may_name_a_city_the_assistant_does_not_know() -> None:
    assert _origin("My departure city is Leeds", stored=HK) == "Leeds"
    assert _origin("Change the origin to Leeds", stored=HK) == "Leeds"
    assert _origin("Use Leeds as the departure city", stored=HK) == "Leeds"
    assert _origin("My departure city is Friday", stored=HK) == HK
    assert _origin("Change the origin to the beginning", stored=HK) == HK


@pytest.mark.parametrize(
    "text",
    [
        "We are travelling from Monday to Friday",
        "Move the museum from Monday to Tuesday",
        "Our travel dates are from Friday to Sunday",
        "Take me to Home",
    ],
)
def test_a_weekday_or_home_is_neither_the_departure_city_nor_the_destination(text: str) -> None:
    context = TripContext(session_id="s", destination="Kyoto", origin=HK)

    _apply_rules(text, context, TODAY)

    assert context.origin == HK and context.destination == "Kyoto"


def test_a_place_named_after_a_weekday_is_still_a_place() -> None:
    context = TripContext(session_id="s")

    _apply_rules("Plan 3 days in Friday Harbor", context, TODAY)

    assert context.destination == "Friday Harbor"


@pytest.mark.parametrize(
    "text",
    [
        "use  as the origin",
        "Use  as the departure city",
        "use as the origin",
        "The origin island is Honshu",
        "The origin issues are many",
        "Change the origin",
        "departure city",
        "the departure city is",
        "origin:",
        "Use Friday as the departure city",
    ],
)
def test_a_statement_with_no_city_in_it_names_none(text: str) -> None:
    assert _origin(text) == "" and _origin(text, stored=HK) == HK


def test_the_destination_is_never_taken_for_the_departure_city() -> None:
    assert _origin("Show me a plan from Kyoto", destination="Kyoto") == ""
    assert _origin("Plan Kyoto from Kyoto", destination="Kyoto") == ""


def test_a_planning_request_still_fills_a_missing_departure_city_but_never_replaces_one() -> None:
    for text in ("Plan Kyoto from Singapore", "Plan 4 days in Kyoto from Singapore", "I want 4 days in Kyoto from Singapore",
                 "Kyoto for 4 days, from Singapore please", "Please plan Kyoto from Singapore"):
        assert _origin(text) == "Singapore", text
        assert _origin(text, stored=HK) == HK, text


def test_a_request_to_the_assistant_is_not_a_question_even_with_a_question_mark() -> None:
    assert _origin("Could you plan 3 days in Rome from London please?") == "London"
    assert _origin("Can you plan Kyoto from Hong Kong?") == HK
    assert _origin("Could you plan 3 days in Rome from London please?", stored="Singapore") == "Singapore"
    assert _origin("Is 3 days in Rome from London enough?") == ""
    assert _origin("Plan Kyoto from Hong Kong?") == ""


def test_the_day_trips_and_the_trips_between_two_cities_are_told_apart() -> None:
    assert _origin("a 5-day trip from Leeds to Paris") == "Leeds"
    assert _origin("a 3-day trip to Kyoto from Singapore") == "Singapore"
    assert _origin("a day trip from Leeds to Paris") == ""
    assert _origin("a one-day trip from Kyoto to Nara") == ""
    assert _origin("a half-day trip from Osaka") == ""


def test_a_reader_that_reads_hostile_text_takes_time_in_proportion_to_it() -> None:
    import time

    texts = [
        "from " * 1_200,
        "flying from " * 500,
        "from a " * 800,
        "trip from Kyoto, " * 350,
        ("I am flying from " + "Leeds and " * 600),
        "Plan " + "from Kyoto " * 500,
        "from " + "a" * 5_990,
    ]
    for text in texts:
        started = time.process_time()
        _origin(text)
        assert time.process_time() - started < 2.0, text[:30]


@pytest.mark.parametrize(
    "unit",
    [
        "3 days from Hong Kong ",
        "Plan from Kyoto from Kyoto ",
        "from Tokyo to Rome ",
        "how far is it from Aaa? ",
        "Plan 3 days in Rome from Osaka ",
    ],
)
def test_a_trip_with_a_departure_city_reads_a_long_message_of_from_phrases_in_time_in_proportion_to_it(unit: str) -> None:
    """Every "from" phrase is read against the sentence around it, and the work for one is bounded by the look-back
    window, not by the length of the sentence: 96,000 characters without a full stop were once 10 seconds."""
    import time

    text = (unit * 96_000)[:96_000]
    started = time.process_time()

    _origin(text, stored=HK)

    assert time.process_time() - started < 2.0, unit


def _gateway_for_tests() -> LLMGateway:
    settings = Settings(_env_file=None, environment="test", llm_api_key="", llm_base_url="", xai_api_key="")
    return _gateway(settings)
