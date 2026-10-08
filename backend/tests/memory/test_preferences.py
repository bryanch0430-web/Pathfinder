from __future__ import annotations

from backend.memory.preferences import merge_preferences
from backend.schemas.common import Money
from backend.schemas.memory import PreferenceProfile
from backend.tests.memory.helpers import make_context, make_plan


def test_context_sets_hotel_style_budget_and_party_hints() -> None:
    profile = PreferenceProfile(hotel_style="hostel", party_size_hint=1)
    context = make_context(
        hotel_style="ryokan", party_size=3, budget=Money(amount=900, currency="EUR")
    )
    merged = merge_preferences(profile, context=context)
    assert merged.hotel_style == "ryokan"
    assert merged.party_size_hint == 3
    assert merged.budget_hint == Money(amount=900, currency="EUR")


def test_unset_context_fields_keep_the_profile_values() -> None:
    profile = PreferenceProfile(
        hotel_style="hostel", party_size_hint=1, budget_hint=Money(amount=50, currency="USD")
    )
    merged = merge_preferences(
        profile, context=make_context(hotel_style=None, party_size=None, budget=None)
    )
    assert merged == profile


def test_liked_plan_adds_categories_most_recent_first_without_duplicates() -> None:
    profile = PreferenceProfile(liked_categories=["museum", "Shrine", "market"])
    plan = make_plan("Kyoto")  # shrine, temple, garden
    merged = merge_preferences(profile, liked_plan=plan)
    assert merged.liked_categories == ["shrine", "temple", "garden", "museum", "market"]


def test_liked_categories_are_capped_at_ten() -> None:
    places = [(f"Place {i}", f"cat{i}") for i in range(8)]
    plan = make_plan("Kyoto", places=places)
    profile = PreferenceProfile(liked_categories=[f"old{i}" for i in range(8)])
    merged = merge_preferences(profile, liked_plan=plan)
    assert len(merged.liked_categories) == 10
    assert merged.liked_categories[:8] == [f"cat{i}" for i in range(8)]  # newest first
    assert merged.liked_categories[8:] == ["old0", "old1"]


def test_avoid_constraints_feed_avoided_categories() -> None:
    profile = PreferenceProfile(avoided_categories=["nightclub"])
    context = make_context(avoid=["casino", "Nightclub"], must_visit=["Fushimi Inari"])
    merged = merge_preferences(profile, context=context)
    assert merged.avoided_categories == ["casino", "Nightclub"]
    assert merged.liked_categories == []  # must-visit is not a "liked category"


def test_merge_is_pure() -> None:
    profile = PreferenceProfile(liked_categories=["museum"])
    context = make_context(hotel_style="ryokan")
    plan = make_plan("Kyoto")
    snapshot = profile.model_copy(deep=True)
    merged = merge_preferences(profile, context=context, liked_plan=plan)
    assert profile == snapshot
    assert merged is not profile
    merged.liked_categories.append("mutated")
    assert profile.liked_categories == ["museum"]
