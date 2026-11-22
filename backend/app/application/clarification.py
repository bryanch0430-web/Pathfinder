"""Clarification: one specific question, no planning. A look-up may run first."""

from __future__ import annotations

import json
import re
from typing import Any, Sequence

from app.application.llm_gateway import LLMGateway
from app.application.prompt_context import conversation_block, plan_outline, trip_state, with_conversation
from app.domain.errors import CanaryLeak
from app.domain.models import Message, RouteDecision, TripContext, TripPlan
from app.security import fence_user_message, strip_fence_tags, strip_fence_tags_deep

SLOT_PHRASES = {
    "destination": "where you want to go",
    "origin": "where you are travelling from",
    "dates": "your travel dates",
    "start_date": "your travel dates",
    "end_date": "your travel dates",
    "party_size": "how many people are travelling",
    "budget": "your budget",
}

SLOT_LABELS = {
    "destination": "Destination",
    "origin": "Departure city",
    "dates": "Travel dates",
    "start_date": "Start date",
    "end_date": "End date",
    "party_size": "Travellers",
    "budget": "Budget",
    "item_to_remove": "Which stop?",
}


#: the reply to an unclear message when there is no plan and no stop to ask about
NO_PLAN_LINE = "I have the trip details. Could you say what you want me to plan or change?"
NO_PLAN_TO_MODIFY_LINE = (
    "There is no plan yet to change. Tell me your trip details and I will build one first."
)
#: how much of the plan's outline a question about it carries
PLAN_OUTLINE_CHARS = 1800


def _names_something_on(plan: TripPlan, message: str) -> bool:
    """True when the message names a stop, stay, city, leg place, day title or "day N" of the plan."""
    text = message.casefold()
    names = [item.name for day in plan.days for item in day.items] + [day.title for day in plan.days]
    names += [hotel.name for hotel in plan.hotels] + [segment.destination.name for segment in plan.segments]
    names += [place for leg in plan.legs for place in (leg.from_place, leg.to_place)]
    if any(len(name.strip()) >= 3 and name.strip().casefold() in text for name in names):
        return True
    return any(0 < int(n) <= len(plan.days) for n in re.findall(r"\bday\s*(\d+)\b", text))


class Clarifier:
    def __init__(self, gateway: LLMGateway, settings: Any | None = None):
        self.gateway = gateway
        self.settings = settings

    async def ask(
        self,
        *,
        message: str,
        context: TripContext,
        decision: RouteDecision,
        history: Sequence[Message] = (),
        plan: TripPlan | None = None,
        scope_item_id: str | None = None,
        notes: str = "",
    ) -> tuple[str, list[str]]:
        from app.application.understanding import figure_warning

        warning = figure_warning(message)
        if warning:
            missing = context.missing_slots() or decision.missing_slots or ["party_size"]
            return f"{warning} {self._fallback(missing)}", missing
        if decision.reason_code in {"unknown_place", "unchecked_place", "suggest_place", "impossible_place", "ambiguous_place"}:
            slot = "origin" if "origin" in decision.missing_slots else "destination"
            if decision.rationale.strip():
                return decision.rationale.strip(), [slot]
            name = "that"
            if decision.reason_code == "unchecked_place":
                return f"I couldn't check {name} just now. Tell me the city again.", [slot]
            if slot == "origin":
                return f"I can't find a place called {name}. Which city are you leaving from?", ["origin"]
            return f"I can't find a place called {name}. Which city should the trip go to?", ["destination"]
        missing = context.missing_slots() or decision.missing_slots
        normalised: list[str] = []
        for slot in missing:
            label = "dates" if slot in {"start_date", "end_date"} else slot
            if label not in normalised:
                normalised.append(label)
        missing = normalised
        if plan is None and decision.reason_code == "no_plan_to_modify":
            if missing:
                return f"There is no plan yet to change. {self._fallback(missing)}", missing
            return NO_PLAN_TO_MODIFY_LINE, []
        if not missing:
            if plan is not None and self.gateway.available and (scope_item_id or _names_something_on(plan, message)):
                # Nothing is missing, the message is unclear and it names a stop, stay, day, city or leg of the plan:
                # ask about that plan. A message that names nothing on it gets the fixed line, with no model call.
                question = await self._about_the_plan(message, plan, scope_item_id, decision, history, notes)
                if question:
                    return question, []
            return NO_PLAN_LINE, []
        if self.gateway.available:
            looked = f"Looked up:\n{notes}\n\n" if notes else ""
            user = (
                looked
                + f"Known context: {trip_state(context)}\n"
                f"Missing slots: {strip_fence_tags_deep(missing)}\n"
                f"Router reason: {strip_fence_tags(decision.reason_code)}\n\n"
                + with_conversation(conversation_block(history, message, settings=self.settings))
                + fence_user_message(message)
            )
            try:
                data = await self.gateway.json("clarify", user=user, purpose="clarify")
                question = str(data.get("question") or "").strip()
                slots = [str(slot) for slot in data.get("slots") or missing]
                if question and _covers(question, missing):
                    return question, slots or missing
            except CanaryLeak:
                raise
            except Exception:
                pass
        return self._fallback(missing), missing

    async def _about_the_plan(
        self,
        message: str,
        plan: TripPlan,
        scope_item_id: str | None,
        decision: RouteDecision,
        history: Sequence[Message],
        notes: str = "",
    ) -> str:
        """One question about the plan (or two or three concrete options) for an unclear message; "" when the writer
        gives none. The plan's names come from the trip form and from place sources: they lose any fence tag, as the
        outline every other prompt carries does, and the message is sent once, inside its fence."""
        outline = plan_outline(plan.summary_for_prompt(), times=False)[:PLAN_OUTLINE_CHARS]
        selected = ""
        found = plan.find_item(scope_item_id) if scope_item_id else None
        if found is not None:
            day, item = found
            selected = f"Day {plan.days.index(day) + 1} · {item.name}"
        else:
            hotel = plan.find_hotel(scope_item_id) if scope_item_id else None
            if hotel is not None:
                selected = f"Hotel · {hotel.name}"
        looked = f"Looked up:\n{notes}\n\n" if notes else ""
        user = (
            looked
            + "Nothing is missing from the trip details; the message is unclear. Ask about the current plan.\n"
            f"Current plan:\n{outline}\n"
            f"Selected on the page: {strip_fence_tags(selected) or 'nothing'}\n"
            f"Router reason: {strip_fence_tags(decision.reason_code)}\n\n"
            + with_conversation(conversation_block(history, message, settings=self.settings))
            + fence_user_message(message)
        )
        try:
            data = await self.gateway.json("clarify", user=user, purpose="clarify")
        except CanaryLeak:
            raise
        except Exception:
            return ""
        return " ".join(str(data.get("question") or "").split())[:400]

    @staticmethod
    def _fallback(missing: list[str]) -> str:
        normalized: list[str] = []
        for slot in missing:
            label = "dates" if slot in {"start_date", "end_date"} else slot
            if label not in normalized:
                normalized.append(label)
        phrases = [SLOT_PHRASES.get(slot, slot) for slot in normalized[:3]]
        if len(phrases) == 1:
            return f"Could you tell me {phrases[0]}?"
        if len(phrases) == 2:
            return f"Could you tell me {phrases[0]} and {phrases[1]}?"
        return f"Could you tell me {', '.join(phrases[:-1])} and {phrases[-1]}?"


_CUES = {
    "origin": ("from", "origin", "depart", "leaving", "leave"),
    "destination": ("where", "destination", "city", "visit"),
    "dates": ("when", "date", "day"),
    "start_date": ("when", "date", "day"),
    "end_date": ("when", "date", "day"),
    "budget": ("budget", "spend", "cost", "money"),
    "party_size": ("people", "person", "party", "traveller", "traveler"),
}


def _covers(question: str, missing: list[str]) -> bool:
    """True when the question names every detail that is still empty."""
    lower = question.lower()
    for slot in missing:
        cues = _CUES.get(slot)
        if cues and not any(re.search(rf"\b{re.escape(cue)}\b", lower) for cue in cues):
            return False
    return True
