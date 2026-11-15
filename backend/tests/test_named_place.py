"""A named place is what gets searched and, when no stop is being changed, what gets added."""

from app.application.modification import (
    ChangeDraft,
    _add_attractions,
    _fresh_places,
    _matches_name,
    _named_place,
    _places_query,
    _wants_a_new_stop,
)
from app.infrastructure.providers.places import _query_is_a_kind
from tests.plan_samples import make_plan

_SKY = {
    "id": "sky",
    "name": "Shibuya Sky",
    "description": "Observation deck",
    "source_refs": [{"title": "Shibuya Sky", "url": "https://example.com/sky", "source": "test"}],
}
_OTHER = {
    "id": "other",
    "name": "Sky Garden",
    "description": "A garden",
    "source_refs": [{"title": "Sky Garden", "url": "https://example.com/garden", "source": "test"}],
}


def test_the_sentences_from_the_chat_name_shibuya_sky() -> None:
    assert _named_place("want day 1 have the attraction shibuya sky") == "shibuya sky"
    assert _named_place("change it to shibuya sky") == "shibuya sky"
    assert _named_place("add Shibuya Sky on day 2") == "Shibuya Sky"
    assert _named_place("add a museum") is None
    assert _named_place("Add a museum near my home in Mong Kok") is None
    assert _places_query("change it to shibuya sky") == "shibuya sky"
    assert _places_query("add a museum") == "museums"
    assert _query_is_a_kind("museums")
    assert not _query_is_a_kind("shibuya sky")


def test_a_named_place_on_day_1_is_added_there_and_a_change_is_not_an_add() -> None:
    plan = make_plan(days=2, with_tickets=False)
    draft = ChangeDraft(description="")
    message = "want day 1 have the attraction shibuya sky"
    assert _wants_a_new_stop(message, plan, draft)
    assert not _wants_a_new_stop("change it to shibuya sky", plan, draft)
    assert [row["name"] for row in _fresh_places(plan, [_OTHER, _SKY], message)] == ["Shibuya Sky"]
    added = _add_attractions(plan, [_OTHER, _SKY], message)
    assert added == ["Shibuya Sky"]
    assert any(item.name == "Shibuya Sky" for item in plan.days[0].items)
    assert all(item.name != "Shibuya Sky" for item in plan.days[1].items)


def test_a_chinese_name_keeps_the_source_result_and_a_one_letter_typo_still_matches() -> None:
    message = "can you add 合味道紀念館 橫濱 to day 3"
    assert _named_place(message) == "合味道紀念館 橫濱"
    assert _matches_name("合味道紀念館 橫濱", "合味道紀念館")
    assert _matches_name("Shibuya Sky", "shubuya sky")
    assert not _matches_name("Sky Garden", "Shibuya Sky")
    museum = {
        "id": "noodles",
        "name": "Cup Noodles Museum",
        "description": "Yokohama",
        "source_refs": [{"title": "Cup Noodles Museum", "url": "https://example.com/noodles", "source": "test"}],
    }
    plan = make_plan(days=3, with_tickets=False)
    assert [row["name"] for row in _fresh_places(plan, [museum], message)] == ["Cup Noodles Museum"]
