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
