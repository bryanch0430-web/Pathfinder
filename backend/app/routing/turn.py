"""Turn helpers: classify a message, and the words the planner is allowed to change."""

from __future__ import annotations

import re
from app.routing.models import Intent, RouteDecision, TripSnapshot
from app.routing.rules import looks_like_new_trip
from app.routing.system1 import build_router

_SKIP = object()


def prepare(payload: dict | None) -> RouteDecision | object | None:
    """An explicit button. None means the message still needs the router. SKIP means leave the form flow alone."""
    action = (payload or {}).get("action")
    if action in {"SUBMIT_TRIP_ONBOARDING", "SHOW_ONBOARDING_FORM"}:
        return _SKIP
    if action in {"GENERATE_FULL_ITINERARY", "START_BUILD_WITH_AGENT"}:
        return RouteDecision(
            intent=Intent.PLAN, confidence=1.0, need_clarification=False, reason_code="explicit_plan",
            related_to_plan=False, rationale="The traveler chose this path.", source="explicit",
        )
    if action == "COPILOT_OPS":
        days = []
        for op in (payload or {}).get("ops") or []:
            if isinstance(op, dict) and op.get("day"):
                try:
                    days.append(f"day_{int(op['day'])}")
                except (TypeError, ValueError):
                    continue
        parts = sorted(set(["attraction", *days]))
        return RouteDecision(
            intent=Intent.MODIFY, confidence=1.0, need_clarification=False, reason_code="explicit_edit",
            affected_parts=parts, related_to_plan=True, rationale="A structured edit.", source="explicit",
        )
    return None


def should_skip(prepared: object) -> bool:
    return prepared is _SKIP


async def classify(
    message: str,
    *,
    destination: str | None,
    origin: str | None,
    duration_days: int | None,
    has_plan: bool,
    days: list[dict] | None,
    last_reply: str | None,
    history: list[dict[str, str]] | None = None,
    start_date: str | None = None,
    currency: str | None = None,
) -> RouteDecision:
    snapshot = TripSnapshot(
        destination=destination, origin=origin, duration_days=duration_days,
        start_date=start_date, currency=currency, has_plan=has_plan,
    )
    summary = {"destination": destination, "days": days or []} if has_plan else None
    return await build_router().route(
        message=message, snapshot=snapshot, plan_summary=summary, history=history, last_reply=last_reply,
    )


def mode_for(decision: RouteDecision | None, message: str, *, has_plan: bool) -> str:
    """draft builds or rebuilds. revise edits in place. answer does not touch the plan. clarify asks first."""
    if decision is None:
        return "draft"
    if decision.need_clarification:
        return "clarify"
    if decision.intent == Intent.ASK:
        return "answer"
    if decision.intent == Intent.PLAN and has_plan and not _wants_full_replan(message):
        return "revise"
    if decision.intent == Intent.MODIFY and has_plan:
        return "revise"
    if decision.intent == Intent.PLAN and has_plan and _wants_full_replan(message):
        return "rebuild"
    return "draft"


def _wants_full_replan(message: str) -> bool:
    return looks_like_new_trip(message) or bool(re.search(r"\b(start over|from scratch|re-?plan|plan again)\b", message or "", re.I))


def clarify_text(decision: RouteDecision) -> str:
    labels = {"destination": "where you're going", "origin": "where you're leaving from", "duration": "how many days you have"}
    if decision.missing_slots:
        need = ", ".join(labels.get(slot, slot) for slot in decision.missing_slots)
        return f"I can plan that once I know {need}."
    if decision.reason_code == "no_affected_part":
        return "I want to change as little as possible. Which day, place, or part should I touch?"
    return "I want to be sure before I change the trip. Are you asking about the plan, or should I change something in it?"


def scope_instructions(parts: list[str]) -> str:
    if not parts:
        return ""
    shown = ", ".join(part.replace("_", " ") for part in parts)
    return (
        "MINIMAL CHANGE:\n"
        f"Touch only these parts: {shown}.\n"
        "Copy every other day and every other kind of stop exactly, including its clock times. "
        "Do not add, remove, or rename anything outside that scope.\n"
        "If you move a place onto one day, take it off the day it was on.\n\n"
    )


def stage_label(decision: RouteDecision) -> str:
    if decision.need_clarification:
        return "Making sure I understood"
    if decision.intent == Intent.ASK:
        return "Answering from your plan"
    if decision.intent == Intent.MODIFY:
        days = [part.replace("day_", "day ") for part in decision.affected_parts if part.startswith("day_")]
        return "Changing " + (", ".join(days) if days else "only what you asked")
    if decision.intent == Intent.PLAN and decision.reason_code != "explicit_plan":
        return "Planning a new trip"
    return "Planning the trip"


def snapshot_from(destination, origin, duration_days, has_plan, start_date=None, currency=None) -> TripSnapshot:
    return TripSnapshot(
        destination=destination, origin=origin, duration_days=duration_days,
        start_date=start_date, currency=currency, has_plan=has_plan,
    )


def plan_names(days: list[dict] | None) -> list[str]:
    names = []
    for day in days or []:
        if isinstance(day, dict):
            names.extend(str(item.get("name") or "") for item in day.get("items") or [] if isinstance(item, dict))
    return names


async def lookup_block(path: str, snapshot: TripSnapshot, message: str, parts: list[str], names: list[str]) -> str:
    from app.routing.tools import tool_notes
    try:
        return await tool_notes(path, snapshot, message, parts, names=names)
    except Exception:
        return ""
