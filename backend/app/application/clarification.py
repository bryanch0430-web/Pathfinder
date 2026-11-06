"""Clarification: one specific question, no planning, no tools."""

from __future__ import annotations

import json
from typing import Sequence

from app.application.llm_gateway import LLMGateway
from app.application.prompt_context import conversation_block, plan_outline, trip_state, with_conversation
from app.domain.errors import CanaryLeak
from app.domain.models import Message, RouteDecision, TripContext, TripPlan
from app.security import fence_user_message, strip_fence_tags, strip_fence_tags_deep

SLOT_PHRASES = {
    "destination": "where you want to go",
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
#: how much of the plan's outline a question about it carries
PLAN_OUTLINE_CHARS = 1800


class Clarifier:
    def __init__(self, gateway: LLMGateway):
        self.gateway = gateway

    async def ask(
        self,
        *,
        message: str,
        context: TripContext,
        decision: RouteDecision,
        history: Sequence[Message] = (),
        plan: TripPlan | None = None,
        scope_item_id: str | None = None,
    ) -> tuple[str, list[str]]:
        missing = context.missing_slots() or decision.missing_slots
        normalised: list[str] = []
        for slot in missing:
            label = "dates" if slot in {"start_date", "end_date"} else slot
            if label not in normalised:
                normalised.append(label)
        missing = normalised
        if not missing:
            if plan is not None and self.gateway.available:
                # Nothing is missing, but the message is unclear and there is a plan: ask about that plan.
                question = await self._about_the_plan(message, plan, scope_item_id, decision, history)
                if question:
                    return question, []
            return NO_PLAN_LINE, []
        if self.gateway.available:
            user = (
                f"Known context: {trip_state(context)}\n"
                f"Missing slots: {strip_fence_tags_deep(missing)}\n"
                f"Router reason: {strip_fence_tags(decision.reason_code)}\n\n"
                + with_conversation(conversation_block(history, message))
                + fence_user_message(message)
            )
            try:
                data = await self.gateway.json("clarify", user=user, purpose="clarify")
                question = str(data.get("question") or "").strip()
                slots = [str(slot) for slot in data.get("slots") or missing]
                if question:
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
        user = (
            "Nothing is missing from the trip details; the message is unclear. Ask about the current plan.\n"
            f"Current plan:\n{outline}\n"
            f"Selected on the page: {strip_fence_tags(selected) or 'nothing'}\n"
            f"Router reason: {strip_fence_tags(decision.reason_code)}\n\n"
            + with_conversation(conversation_block(history, message))
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
