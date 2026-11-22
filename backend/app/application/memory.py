"""Long-term memory about one traveller, derived by the model from a finished trip.

The old rule-based version remembered little more than a destination name. Here a
short model call reads the plan and the conversation and extracts durable statements
("prefers museums over theme parks", "vegetarian", "slow pace", "mid-range stays")
plus the traveller profile fields. Those statements are injected into the trip
context on every later turn, so the analyst and the planner both see them.
"""

from __future__ import annotations

from typing import Any, Literal

from app.domain.models import ChatMessage, TripContext, TripPlan, TravellerProfile
from app.understand.schemas import Draft


class MemoryDraft(Draft):
    preferences: list[str] = []
    pace: Literal["relaxed", "balanced", "packed"] | None = None
    with_children: bool | None = None
    purpose: Literal["leisure", "business"] | None = None
    step_free: bool | None = None


def merge_preferences(existing: list[str], incoming: list[str]) -> tuple[list[str], list[str]]:
    merged = list(existing)
    seen = {item.strip().lower() for item in merged}
    added: list[str] = []
    for value in incoming:
        text = " ".join(str(value).split()).strip()
        if not text or len(text) > 160:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(text)
        added.append(text)
    return merged[:24], added


async def derive_memory(
    *,
    llm: Any,
    plan: TripPlan,
    context: TripContext,
    messages: list[ChatMessage],
) -> MemoryDraft | None:
    """One small call: durable facts only, no trip-specific dates or bookings."""
    from app.core.llm import LLMError

    payload = {
        "traveller": {
            "preferences": context.preferences[:12],
            "hard_constraints": context.hard_constraints[:8],
            "profile": context.profile.model_dump() if context.profile else None,
            "party_size": context.party_size,
        },
        "plan": {
            "destination": plan.destination.name,
            "summary": plan.summary,
            "stays": [hotel.name for hotel in plan.hotels[:2]],
            "stops": [item.name for day in plan.days for item in day.items][:24],
            "tags": sorted({tag for day in plan.days for item in day.items for tag in item.tags})[:20],
        },
        "recent_turns": [
            {"role": message.role, "text": (message.content or "")[:300]}
            for message in messages[-8:]
            if message.role == "user"
        ],
    }
    try:
        return await llm.json(
            purpose="memory",
            system=(
                "You maintain a traveller's long-term travel memory. From the finished trip and the "
                "traveller's own words, extract only DURABLE preferences that should shape future trips: "
                "interests, pace, dietary needs, accessibility, hotel style, dislikes, travelling style. "
                "Do not store dates, prices, bookings, names of companions, or anything that changes per trip. "
                "Write each preference as a short sentence in the traveller's language, at most 6. "
                "Also fill the profile fields when the evidence is clear, else null. "
                'Reply as JSON: {"preferences": ["..."], "pace": "relaxed|balanced|packed|null", '
                '"with_children": true|false|null, "purpose": "leisure|business|null", "step_free": true|false|null}'
            ),
            user=__import__("json").dumps(payload, ensure_ascii=False),
            schema=MemoryDraft,
        )
    except LLMError:
        return None


async def remember_trip(
    *,
    container: Any,
    identity: str,
    plan: TripPlan,
    context: TripContext,
    session_id: str,
) -> list[str]:
    """Derive memory, merge it into the traveller's profile, and return what was new."""
    if not identity:
        return []
    from app.api.turn import effective_settings
    from app.core.llm import LLMClient

    messages = await container.store.recent_messages(session_id, limit=10)
    # Use the runtime model map so the memory part follows the Models tab too.
    llm = LLMClient(await effective_settings(container))
    try:
        draft = await derive_memory(llm=llm, plan=plan, context=context, messages=[ChatMessage(**row) for row in messages])
    finally:
        await llm.aclose()
    if draft is None:
        return []
    profile = await container.store.get_profile(identity) or {"preferences": [], "hard_constraints": []}
    merged, added = merge_preferences(profile["preferences"], draft.preferences)
    await container.store.save_profile(identity, merged, profile.get("hard_constraints", []))
    if draft.pace or draft.with_children is not None or draft.purpose or draft.step_free is not None:
        base = context.profile.model_dump() if context.profile else TravellerProfile().model_dump()
        if draft.pace:
            base["pace"] = draft.pace
        if draft.with_children is not None:
            base["with_children"] = draft.with_children
        if draft.purpose:
            base["purpose"] = draft.purpose
        if draft.step_free is not None:
            base["step_free"] = draft.step_free
        context.profile = TravellerProfile(**base)
        await container.store.save_context(session_id, context.model_dump())
    return added


def merged_context_memory(context: TripContext, profile: dict[str, Any] | None) -> TripContext:
    """Copy the stored preferences into this turn's context (without duplicates)."""
    if not profile:
        return context
    merged, _added = merge_preferences(context.preferences, profile.get("preferences") or [])
    constraints = list(context.hard_constraints)
    for value in profile.get("hard_constraints") or []:
        if value and value not in constraints:
            constraints.append(value)
    context.preferences = merged
    context.hard_constraints = constraints[:16]
    return context
