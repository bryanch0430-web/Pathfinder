"""Clarification: one specific question, no planning, no tools."""

from __future__ import annotations

import json
from typing import Sequence

from app.application.llm_gateway import LLMGateway
from app.application.prompt_context import conversation_block, trip_state, with_conversation
from app.domain.errors import CanaryLeak
from app.domain.models import Message, RouteDecision, TripContext
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
    ) -> tuple[str, list[str]]:
        missing = context.missing_slots() or decision.missing_slots
        normalised: list[str] = []
        for slot in missing:
            label = "dates" if slot in {"start_date", "end_date"} else slot
            if label not in normalised:
                normalised.append(label)
        missing = normalised
        if not missing:
            return "I have the trip details. Could you say what you want me to plan or change?", []
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
