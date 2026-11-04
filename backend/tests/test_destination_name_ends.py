"""A destination the assistant does not know by name ends where its name ends.

"3 days in Nagoya please" was read as the destination "Nagoya Please", which no map knows, and "I want to go to Porto
again" as "Go To Porto Again". The name is cut at the first word that ends a name, and the next cue is tried when the
first one names no place.
"""

from __future__ import annotations

import pytest

from app.application.understanding import _find_place

CUES = ["to", "in", "visit", "explore", "around"]


@pytest.mark.parametrize(
    ("sentence", "place"),
    [
        ("3 days in nagoya please", "Nagoya"),
        ("a trip to lisbon instead", "Lisbon"),
        ("2 days in nara now", "Nara"),
        ("i want to go to porto again", "Porto"),
        ("we would love to visit lyon too", "Lyon"),
        ("go to isle of skye for a week", "Isle Of Skye"),
        ("plan 4 days in rio de janeiro", "Rio De Janeiro"),
        ("a weekend in newcastle upon tyne", "Newcastle Upon Tyne"),
        ("trip to bangkok, thailand", "Bangkok"),
    ],
)
def test_the_name_of_a_place_ends_where_it_ends(sentence: str, place: str) -> None:
    assert _find_place(sentence, CUES) == place


@pytest.mark.parametrize("sentence", ["i want to go to", "i would love to go", "go to now", "in a week"])
def test_a_cue_with_no_place_after_it_names_none(sentence: str) -> None:
    assert _find_place(sentence, CUES) is None


@pytest.mark.parametrize(
    "sentence",
    [
        "i want 6 days in total",
        "only 2 days in total",
        "i only have 2 days in total",
        "only 2 days in the city",
        "let's do 4 days in the city",
        "forget the day trip, we will stay in the city",
        "ignore the signs in the old town, they are mostly wrong",
        "swap the hotel for something closer to the station",
        "is venice worth visiting in winter?",
        "keep the budget in general",
        "book it well in advance",
        "all prices in euros please",
        "reply in french",
    ],
)
def test_a_noun_that_is_no_place_is_not_read_as_the_destination(sentence: str) -> None:
    """"6 days in total" was a new trip to Total and "only 2 days in the city" one to The City."""
    assert _find_place(sentence, CUES) is None


@pytest.mark.parametrize(
    ("sentence", "place"),
    [
        ("let's do 4 days in kyoto with a day trip to osaka", "Kyoto"),
        ("let's do 4 days in kyoto and a day trip to nara", "Kyoto"),
        ("3 days in rome, then a train to florence", "Rome"),
        ("a trip to porto and then 2 days in lisbon", "Porto"),
        ("visit lyon, then go to dijon", "Lyon"),
    ],
)
def test_the_first_place_a_sentence_names_is_the_destination_whichever_cue_comes_first_in_the_list(sentence: str, place: str) -> None:
    """The cues were tried in the order of the list, so a later "to Osaka" beat an earlier "in Kyoto"."""
    assert _find_place(sentence, CUES) == place


@pytest.mark.parametrize(
    ("sentence", "place"),
    [
        ("stopover in dubai on the way from london to sydney", "Sydney"),
        ("stopover in hong kong on the way to london", "London"),
        ("a layover in tokyo, then 5 days in seoul", "Seoul"),
        ("layover in doha from 2pm to 11pm", "Doha"),
    ],
)
def test_a_stopover_is_not_the_destination(sentence: str, place: str) -> None:
    assert _find_place(sentence, CUES) == place


@pytest.mark.parametrize(
    ("sentence", "place"),
    [
        ("3 days in do son", "Do Son"),
        ("a week in go cong", "Go Cong"),
        ("explore go vap", "Go Vap"),
        ("visit be island", "Be Island"),
        ("a weekend in see island", "See Island"),
    ],
)
def test_a_place_whose_first_word_is_a_verb_is_read_after_in_visit_and_explore(sentence: str, place: str) -> None:
    """A verb after "to" is a verb ("love to see Lyon", "want to go"); after "in", "visit" or "explore" it begins a name."""
    assert _find_place(sentence, CUES) == place


@pytest.mark.parametrize("sentence", ["i want to go", "i would love to see", "we want to do", "we would like to try"])
def test_a_verb_after_to_is_still_no_place(sentence: str) -> None:
    assert _find_place(sentence, CUES) is None


@pytest.mark.parametrize(
    ("sentence", "place"),
    [
        ("is tap water safe to drink in mexico city?", "Mexico City"),
        ("is it safe to walk around naples at night?", "Naples"),
        ("pretend you are a local and tell me what to eat", None),
        ("which endpoint of the camino is better to start from, sarria or saint jean?", None),
        ("i want to stop planning for today, we can continue tomorrow", None),
        ("please call the hotel to confirm the late check-in", None),
        ("use the tool at the desk to scan the ticket", None),
    ],
)
def test_a_verb_after_to_is_not_a_destination(sentence: str, place: str | None) -> None:
    """"safe to drink in Mexico City" was a trip to Drink."""
    assert _find_place(sentence, CUES) == place


@pytest.mark.parametrize(
    ("sentence", "place"),
    [
        ("i live in london and want to go to rome", "Rome"),
        ("we are based in paris, planning a week in rome", "Rome"),
        ("i live in tampa and want 4 days in kyoto", "Kyoto"),
        ("i want to live in lisbon for a month", "Lisbon"),
    ],
)
def test_where_the_traveller_lives_is_the_destination_only_when_the_sentence_names_no_other_place(sentence: str, place: str) -> None:
    """The place named first wins, but "I live in London and want to go to Rome" is a trip to Rome."""
    assert _find_place(sentence, CUES) == place
