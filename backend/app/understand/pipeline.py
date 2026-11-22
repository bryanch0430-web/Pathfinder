"""Turn understanding: one model call turns a message into a typed decision.

This module owns the merge of the analyst's slots into :class:`TripContext`.  No
keyword rules decide the intent and no regex pulls slots out of the text: the
analyst reads meaning (including relative dates and the traveller's language)
and returns the schema in ``app.understand.schemas``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.config import Settings
from app.core.dates import finish_dates, parse_date
from app.core.llm import LLMClient, LLMError
from app.core.prompts import context_brief, dialogue, plan_outline, skill
from app.domain.models import ChatMessage, ClarifyPayload, RoutePayload, TripContext, TripPlan
from app.understand.schemas import ParsedIntent

PART_NAMES = {"attraction", "hotel", "weather", "ticket", "meal", "transport"}
CLEARABLE = {
    "destination", "origin", "start_date", "end_date", "days", "party_size",
    "budget", "currency", "hotel_style", "preferences", "hard_constraints",
    "stops", "profile",
}


@dataclass
class Understanding:
    parsed: ParsedIntent
    context: TripContext
    route: RoutePayload
    clarify: ClarifyPayload | None
    blocked: bool = False


async def understand(
    *,
    llm: LLMClient,
    settings: Settings,
    context: TripContext,
    plan: TripPlan | None,
    messages: list[ChatMessage],
    message: str,
    scope_label: str | None,
    scope_item_id: str | None,
    locale: str,
    skill_overrides: dict[str, str] | None = None,
) -> Understanding:
    today = datetime.now().astimezone()
    user_payload: dict[str, Any] = {
        "today": today.date().isoformat(),
        "now_local": today.isoformat(timespec="minutes"),
        "timezone": str(today.tzinfo),
        "interface_locale": locale,
        "threshold": settings.router_threshold,
        "trip": context_brief(context),
        "plan": plan_outline(plan),
        "recent_turns": dialogue(messages),
        "selected_stop": {"id": scope_item_id, "label": scope_label} if scope_item_id or scope_label else None,
        "message": message,
    }
    system = skill("understand", skill_overrides, threshold=settings.router_threshold)
    import json

    try:
        parsed = await llm.json(
            purpose="understand",
            system=system,
            user=json.dumps(user_payload, ensure_ascii=False, indent=1),
            schema=ParsedIntent,
        )
    except LLMError:
        # A garbled analysis must not lose the traveller's message: ask them to rephrase.
        parsed = ParsedIntent(
            intent="unclear",
            confidence=0.0,
            reason_code="analyst_unavailable",
            reason="The message could not be read.",
            need_clarification=True,
            question="I could not make sense of that last message — could you say it another way?",
            missing_slots=["scope"],
        )
    updated = merge_slots(context, parsed)
    updated.language = parsed.language or updated.language
    route = _route(parsed, settings, plan)
    clarify = _clarify(parsed, updated, plan, route) if route.need_clarification else None
    return Understanding(parsed=parsed, context=updated, route=route, clarify=clarify)


def merge_slots(context: TripContext, parsed: ParsedIntent) -> TripContext:
    """Apply the analyst's slot update. Copy forward everything it did not touch."""
    updated = context.model_copy(deep=True)
    slots = parsed.slots

    for field in parsed.clears:
        if field in CLEARABLE:
            setattr(updated, field, [] if field in {"preferences", "hard_constraints", "stops"} else None)

    if slots.destination:
        updated.destination = slots.destination.strip()[:120]
    if slots.origin:
        updated.origin = slots.origin.strip()[:120]
    if slots.start_date:
        updated.start_date = slots.start_date
    if slots.end_date:
        updated.end_date = slots.end_date
    if slots.days is not None:
        updated.days = max(1, min(int(slots.days), 60))
    if slots.party_size is not None:
        updated.party_size = max(1, min(int(slots.party_size), 40))
    if slots.budget is not None:
        updated.budget = float(slots.budget) if float(slots.budget) > 0 else None
    if slots.currency:
        updated.currency = slots.currency.strip().upper()[:8]
    if slots.hotel_style:
        updated.hotel_style = slots.hotel_style.strip()[:120]
    pace = _pace(slots.pace)
    if pace:
        updated.pace = pace
    if slots.preferences:
        updated.preferences = _merge(updated.preferences, slots.preferences)
    removals = [
        str(item).strip().lower()
        for item in [*parsed.remove_constraints, *parsed.clears]
        if str(item).strip()
    ]
    if removals:
        updated.hard_constraints = [
            text
            for text in updated.hard_constraints
            if not any(_same_constraint(text, removal) for removal in removals)
        ]
    if slots.hard_constraints:
        updated.hard_constraints = _merge_constraints(updated.hard_constraints, slots.hard_constraints)
    if slots.stops:
        from app.domain.models import TripStop

        updated.stops = [TripStop(city=stop.city, nights=stop.nights) for stop in slots.stops][:10]
    if any(value is not None for value in (slots.pace, slots.with_children, slots.purpose, slots.step_free)):
        profile = updated.profile.model_dump() if updated.profile else {}
        if pace:
            profile["pace"] = pace
        if slots.with_children is not None:
            profile["with_children"] = slots.with_children
        if slots.purpose in {"leisure", "business"}:
            profile["purpose"] = slots.purpose
        if slots.step_free is not None:
            profile["step_free"] = slots.step_free
        from app.domain.models import TravellerProfile

        try:
            updated.profile = TravellerProfile(**profile)
        except Exception:  # noqa: BLE001 - a malformed profile must not lose the turn
            pass

    start, end, days = finish_dates(
        start=updated.start_date,
        end=updated.end_date,
        days=updated.days if updated.days else None,
    )
    updated.start_date, updated.end_date, updated.days = start, end, days
    if updated.plan_locale not in {"en", "zh-Hant", "zh-Hans"}:
        updated.plan_locale = "en"
    return updated


def _pace(value: str | None) -> str | None:
    if not value:
        return None
    text = str(value).strip().lower()
    if text in {"relaxed", "balanced", "packed"}:
        return text
    if text in {"slow", "slower", "easy", "easy-going", "leisurely", "chill"}:
        return "relaxed"
    if text in {"fast", "faster", "intense", "busy", "full", "dense"}:
        return "packed"
    if text in {"moderate", "normal", "medium", "average"}:
        return "balanced"
    return None


def _same_constraint(text: str, other: str) -> bool:
    first, second = text.strip().lower(), other.strip().lower()
    if not first or not second:
        return False
    return first == second or first in second or second in first


def _merge_constraints(current: list[str], incoming: list[str]) -> list[str]:
    merged = list(current)
    for text in incoming:
        candidate = str(text).strip()
        if not candidate:
            continue
        merged = [row for row in merged if not _same_constraint(row, candidate)]
        merged.append(candidate[:240])
    return merged[:16]


def _merge(current: list[str], incoming: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*current, *incoming]:
        text = str(item).strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        merged.append(text[:200])
    return merged[:16]


def _route(parsed: ParsedIntent, settings: Settings, plan: TripPlan | None) -> RoutePayload:
    intent = parsed.intent
    parts = [part for part in parsed.affected_parts if part in PART_NAMES]
    threshold = settings.router_threshold
    need_clarification = bool(parsed.need_clarification or parsed.intent == "unclear")
    if intent in {"plan", "modify"} and not parsed.slots.destination:
        # A plan with no destination can still be fine when the trip already knows one.
        pass
    if parsed.confidence < threshold and intent in {"plan", "modify", "ask"} and plan is not None:
        # A low-confidence edit on an existing plan is better as a question than a wrong change.
        if intent == "modify":
            need_clarification = True
    return RoutePayload(
        intent=intent,
        confidence=max(0.0, min(1.0, float(parsed.confidence))),
        threshold=threshold,
        need_clarification=need_clarification,
        reason_code=parsed.reason_code or ("low_confidence" if need_clarification else "classified"),
        affected_parts=parts or (["attraction", "hotel", "weather"] if intent == "plan" else []),
        missing_slots=list(parsed.missing_slots)[:6],
        related_to_plan=bool(plan) and intent in {"ask", "modify"},
        blocked=False,
    )


def _clarify(
    parsed: ParsedIntent,
    context: TripContext,
    plan: TripPlan | None,
    route: RoutePayload,
) -> ClarifyPayload:
    slots = list(parsed.missing_slots)[:4]
    question = (parsed.question or "").strip()
    options = {key: [str(value)[:80] for value in values[:6]] for key, values in parsed.options.items() if values}
    if not question:
        if not context.destination:
            question = "Where would you like to go, and roughly when?"
            slots = slots or ["destination", "start_date"]
        elif not context.start_date and not context.days:
            question = "Which dates should I plan for? A start date and a length is enough."
            slots = slots or ["start_date", "days"]
        elif plan is None:
            question = "I can plan that — just tell me the destination and dates."
            slots = slots or ["destination", "start_date"]
        else:
            question = "Could you say a little more about what you want to change?"
            slots = slots or ["scope"]
    return ClarifyPayload(
        question=question[:400],
        slots=slots,
        labels=[_label(slot) for slot in slots],
        options=options,
        choices=[
            {
                "label": str(row.get("label") or row.get("message") or "").strip()[:120],
                # A chip must always be clickable; an empty message sent nothing at all.
                "message": str(row.get("message") or row.get("label") or "").strip()[:300],
            }
            for row in parsed.choices[:4]
            if isinstance(row, dict) and str(row.get("label") or row.get("message") or "").strip()
        ],
        suggestions=[
            {"slot": row.slot, "asked": row.asked, "name": row.name}
            for row in parsed.suggestions[:4]
            if row.slot and row.name
        ],
        reason_code=parsed.reason_code or route.reason_code or "missing_slots",
    )


def _label(slot: str) -> str:
    return {
        "destination": "Destination",
        "origin": "Leaving from",
        "start_date": "Start date",
        "end_date": "End date",
        "days": "Length",
        "party_size": "Travellers",
        "budget": "Budget",
        "currency": "Currency",
        "scope": "What to change",
    }.get(slot, slot.replace("_", " ").title())


def dates_usable(context: TripContext) -> bool:
    start = parse_date(context.start_date)
    return start is not None and bool(context.days or context.end_date)


def missing_for_plan(context: TripContext) -> list[str]:
    missing: list[str] = []
    if not context.destination:
        missing.append("destination")
    if not context.start_date:
        missing.append("start_date")
    if not (context.days or context.end_date):
        missing.append("days")
    return missing


__all__ = ["Understanding", "understand", "merge_slots", "missing_for_plan", "dates_usable", "LLMError"]
