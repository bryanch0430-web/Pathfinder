"""The edit's research field across shapes: list, joined string, count, and form.

The bug this guards: two schools joined into one string became one search, so
neither was found and both were added as unconfirmed stops.
"""

from __future__ import annotations

import asyncio

from app.domain.models import TripContext
from app.evidence.models import Evidence
from app.evidence.providers import _lookup_queries
from app.understand.schemas import EditDraft


def test_a_plain_list_is_one_query_per_place():
    assert _lookup_queries(["HKU", "HKUST"]) == ["HKU", "HKUST"]


def test_a_list_of_six_names_is_kept():
    names = ["HKU", "HKUST", "CUHK", "CityU", "PolyU", "HKBU"]
    assert _lookup_queries(names) == names


def test_more_than_six_names_is_capped():
    names = [f"Place {index}" for index in range(12)]
    assert len(_lookup_queries(names)) == 6


def test_semicolon_string_is_split():
    assert _lookup_queries("The University of Hong Kong; HKUST") == [
        "The University of Hong Kong",
        "HKUST",
    ]


def test_and_joined_with_two_acronyms_is_split():
    joined = (
        "University of Hong Kong (HKU) and "
        "Hong Kong University of Science and Technology (HKUST)"
    )
    assert _lookup_queries(joined)[:2] == ["HKU", "HKUST"]


def test_one_name_containing_and_stays_whole():
    assert _lookup_queries("Hong Kong University of Science and Technology") == [
        "Hong Kong University of Science and Technology"
    ]


def test_one_name_with_one_acronym_also_tries_the_acronym():
    assert _lookup_queries("The University of Hong Kong (HKU)") == [
        "The University of Hong Kong (HKU)",
        "HKU",
    ]


def test_duplicates_and_case_collapse():
    assert _lookup_queries(["HKU", "hku", " HKU "]) == ["HKU"]


def test_blank_and_missing_values_return_nothing():
    assert _lookup_queries("") == []
    assert _lookup_queries(None) == []
    assert _lookup_queries(["", "   "]) == []


def test_a_non_latin_name_is_kept():
    assert _lookup_queries("東京大学") == ["東京大学"]


def test_a_very_long_name_is_capped_at_the_lookup_limit():
    name = "A" * 400
    assert len(_lookup_queries(name)[0]) == 160


def test_the_edit_contract_accepts_a_list():
    draft = EditDraft(needs_research=True, research_places=["HKU", "HKUST"])
    assert draft.research_places == ["HKU", "HKUST"]


def test_an_old_prompt_sending_a_string_still_works():
    draft = EditDraft(needs_research=True, research_name="HKU")
    assert draft.research_places == ["HKU"]


def test_an_explicit_list_wins_over_the_old_string():
    draft = EditDraft(research_places=["a1", "a2"], research_name="ignored")
    assert draft.research_places == ["a1", "a2"]


def test_an_empty_string_adds_nothing():
    assert EditDraft(research_name="   ").research_places == []


class _Stub:
    """Records the names it was asked for and answers one place each."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def lookup_places(self, names, city=""):
        self.seen.extend(names if isinstance(names, list) else [names])
        return [
            Evidence(ref=f"stub-{index}", kind="attraction", name=f"{name} campus", address=city)
            for index, name in enumerate(names if isinstance(names, list) else [names])
        ]

    async def attractions(self, city, limit=10, interests=None):
        return []


def test_research_looks_up_each_place_on_the_edit():
    from app.plan.editor import _research

    stub = _Stub()
    draft = EditDraft(needs_research=True, research_kind="attraction", research_places=["HKU", "HKUST"])
    context = TripContext(session_id="s", destination="Hong Kong")
    found = asyncio.run(_research(stub, context, draft, None))
    assert stub.seen == ["HKU", "HKUST"]
    assert [item.name for item in found] == ["HKU campus", "HKUST campus"]


def test_research_falls_back_to_the_old_string_field():
    from app.plan.editor import _research

    stub = _Stub()
    draft = EditDraft(needs_research=True, research_kind="attraction", research_name="HKU")
    context = TripContext(session_id="s", destination="Hong Kong")
    found = asyncio.run(_research(stub, context, draft, None))
    assert stub.seen == ["HKU"]
    assert [item.name for item in found] == ["HKU campus"]
