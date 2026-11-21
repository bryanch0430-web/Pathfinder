"""A/B on the decomposer's extra step.

The control sends every name that is not already a known city to the map, which is how grounding worked
before the edge step. The treatment asks about a planet, bare Heaven or Hell, or a chain, and does not store it.
Ordinary cities must come out the same on both arms, or the edge step has changed a trip it should not touch.
"""

from __future__ import annotations

import asyncio

from app.application.understanding import confirm_place


class _Hit:
    def __init__(self, name: str) -> None:
        self.name = name


class _Map:
    available = True

    def __init__(self, answers: dict[str, str | None]) -> None:
        self.answers = {key.casefold(): value for key, value in answers.items()}
        self.calls: list[str] = []

    async def geocode(self, name: str, *, country_hint: str = ""):
        self.calls.append(name)
        found = self.answers.get(name.casefold())
        return None if not found else _Hit(found)

    async def match_share(self, name: str) -> float:
        return 0.2


ORDINARY = {
    "Kyoto": "accept",
    "Tokyo": "accept",
    "Osaka": "accept",
    "Nagoya": "suggest",
    "Nagoia": "suggest",
    "Zorpville": "reject",
    "Hell, Norway": "suggest",
}

EDGES = ("Mars", "Jupiter", "Moon", "Heaven", "Hell", "McDonald's", "Starbucks", "Springfield")


def _check(name: str, *, edges: bool):
    gazetteer = _Map(
        {
            "Nagoya": "Nagoya, Japan",
            "Nagoia": "Nagoya, Japan",
            "Zorpville": None,
            "Hell, Norway": "Hell, Norway",
            "Mars": "Mars, Pennsylvania, USA",
            "Jupiter": "Jupiter, Florida, USA",
            "Heaven": "Heaven, Somewhere",
            "Hell": "Hell, Norway",
            "Moon": "Muhu, Saare maakond, Eesti",
            "Springfield": "Springfield, Missouri, USA",
            "McDonald's": "McDonald's, Nathan Road",
            "Starbucks": "Starbucks, Central",
        }
    )
    # Zorpville is stored as None, so a missing key must not be confused with an explicit miss.
    if name == "Zorpville":
        gazetteer.answers["zorpville"] = None
    result = asyncio.run(confirm_place(name, gazetteer, edges=edges))
    return result, gazetteer.calls


def test_ordinary_cities_match_on_both_arms() -> None:
    for name, status in ORDINARY.items():
        control, control_calls = _check(name, edges=False)
        treatment, treatment_calls = _check(name, edges=True)
        assert control.status == treatment.status == status, name
        assert control.name == treatment.name
        assert control_calls == treatment_calls, name


def test_edge_names_are_questions_on_the_treatment_and_mapped_on_the_control() -> None:
    for name in EDGES:
        control, control_calls = _check(name, edges=False)
        treatment, treatment_calls = _check(name, edges=True)
        assert control_calls == [name], name
        assert control.status in {"suggest", "reject"}
        assert treatment_calls == [], name
        assert treatment.status in {"impossible", "ambiguous"}, name
        assert treatment.status != control.status
