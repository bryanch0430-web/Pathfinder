"""Preference profile updates (session memory)."""

from __future__ import annotations

from collections.abc import Iterable

from backend.schemas.memory import PreferenceProfile
from backend.schemas.trip import ConstraintKind, TripContext
from backend.schemas.trip_plan import TripPlan

MAX_LIKED_CATEGORIES = 10
MAX_AVOIDED_CATEGORIES = 20


def _recent_first(new: Iterable[str], old: Iterable[str], cap: int) -> list[str]:
    """`new` items (in their given order) ahead of `old` ones, deduplicated case-insensitively
    (first spelling wins), blanks dropped, truncated to `cap`."""
    seen: set[str] = set()
    merged: list[str] = []
    for item in (*new, *old):
        cleaned = item.strip()
        key = cleaned.casefold()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        merged.append(cleaned)
    return merged[:cap]


def merge_preferences(
    profile: PreferenceProfile,
    *,
    context: TripContext | None = None,
    liked_plan: TripPlan | None = None,
) -> PreferenceProfile:
    """Return an updated copy: hotel_style / budget_hint / party_size_hint from the context when
    set; liked_categories gains the place categories of a plan the user marked useful
    (deduplicated, most recent first, capped at 10); AVOID constraints feed avoided_categories.

    Pure: the input profile, context and plan are never mutated.
    """
    merged = profile.model_copy(deep=True)

    if context is not None:
        if context.hotel_style:
            merged.hotel_style = context.hotel_style
        if context.budget is not None:
            merged.budget_hint = context.budget.model_copy(deep=True)
        if context.party_size is not None:
            merged.party_size_hint = context.party_size
        avoided = [c.value for c in context.hard_constraints if c.kind is ConstraintKind.AVOID]
        if avoided:
            merged.avoided_categories = _recent_first(
                avoided, merged.avoided_categories, MAX_AVOIDED_CATEGORIES
            )

    if liked_plan is not None:
        merged.liked_categories = _recent_first(
            (place.category for place in liked_plan.places),
            merged.liked_categories,
            MAX_LIKED_CATEGORIES,
        )

    return merged
