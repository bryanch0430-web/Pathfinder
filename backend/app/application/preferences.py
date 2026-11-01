"""Derive a small, explainable preference profile from a saved plan.

Only structured fields and explicit traveller choices are used. We do not infer
sensitive attributes, and we do not treat place names as facts about a person.
"""

from __future__ import annotations

from app.domain.models import TripContext, TripPlan


def derive_preferences(plan: TripPlan, context: TripContext | None) -> list[str]:
    tags: list[str] = []

    def add(value: str) -> None:
        value = value.strip().lower()
        if value and value not in tags:
            tags.append(value)

    for preference in (context.preferences if context else [])[:8]:
        add(preference)
    if context:
        if context.pace == "relaxed":
            add("slow pace")
        elif context.pace == "packed":
            add("fast pace")
        if context.hotel_style:
            add(f"{context.hotel_style} stays")
        if context.budget:
            if plan.cost.total <= context.budget * 0.65:
                add("comfortable budget")
            elif plan.cost.total > context.budget:
                add("premium budget")

    for day in plan.days:
        for item in day.items:
            text = f"{item.name} {item.type.value} {' '.join(item.tags)}".lower()
            if item.type.value == "meal":
                add("food")
            if any(word in text for word in ("museum", "gallery", "art")):
                add("museums")
            if any(word in text for word in ("temple", "shrine", "church", "monastery")):
                add("temples")
            if any(word in text for word in ("park", "garden", "nature", "peak", "beach", "island")):
                add("nature")
            if any(word in text for word in ("disney", "theme park", "ocean park", "zoo")):
                add("theme parks")
            if any(word in text for word in ("market", "shopping", "mall", "street")):
                add("markets and shopping")
            if any(word in text for word in ("bar", "night", "club", "pub")):
                add("nightlife")

    return tags[:10]
