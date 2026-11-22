"""The clarification gate: a still-ambiguous answer is asked about again, not planned.

The router is the source of truth for the path (Jev first). This module only tightens the one place
where the gate was too eager: when the confidence is below ``router_threshold`` and the trip form is
complete, the router's gate moves the turn to **plan** as "context_complete", so a traveller's short
fragment after a clarify ("yes", "maybe") can start a whole trip that was never asked for.

``tighten_after_clarification`` keeps that fragment in the clarifying phase when the previous
assistant turn asked an *intent* question (a clarify with no missing slots) and the new message is
only an answer word: the question is asked again with concrete choices. A message that carries a real
action - a plan or change verb, a place, a date, a question - is never held back.
"""

from __future__ import annotations

import re
from typing import Sequence

from app.domain.models import Intent, Message, RouteDecision

#: Answers that say something about the *question* but nothing about the trip.
_BARE_ANSWER = re.compile(
    r"^(?:yes|yes please|yeah|yep|yup|ok|okay|sure|sounds good|fine|no|nope|nah|not really|maybe|perhaps|"
    r"possibly|i don'?t know|dunno|not sure|i'?m not sure|whatever|either|both|neither|hmm+|why\??|what\??|"
    r"im not sure|idk|yes i do|no i don'?t)[.!]*$",
    re.IGNORECASE,
)

#: Something that makes a short message a request rather than an answer word.
_ACTION = re.compile(
    r"\b(?:plan|itinerary|trip|travel|holiday|vacation|book|add|remove|delete|drop|swap|replace|change|"
    r"move|make|keep|cheaper|budget|day|days|night|nights|hotel|flight|date|dates|tomorrow|next week|"
    r"weekend|month|from|to|in|at|on)\b",
    re.IGNORECASE,
)


def last_asked_for_intent(history: Sequence[Message]) -> bool:
    """True when the previous assistant turn was a clarify that asked *what to do*, not which slot to fill."""
    rows = list(history or ())
    if rows and rows[-1].role == "user":
        rows = rows[:-1]
    if not rows or rows[-1].role != "assistant":
        return False
    meta = rows[-1].meta or {}
    return bool(meta.get("clarify")) and not meta.get("slots")


def is_bare_answer(message: str) -> bool:
    """A short answer word with no place, date, verb or number in it."""
    text = (message or "").strip()
    if not text or len(text.split()) > 4:
        return False
    if re.search(r"\d", text):
        return False
    if _ACTION.search(text):
        return False
    return bool(_BARE_ANSWER.match(text))


def tighten_after_clarification(
    decision: RouteDecision,
    *,
    message: str,
    history: Sequence[Message],
) -> RouteDecision:
    """Hold a bare answer to an intent question in the clarifying phase instead of planning from it.

    Only the gate's own ``context_complete`` escape is tightened: a decision the router actually made
    (a real plan, change or question with enough confidence) is returned unchanged, because the
    router's reading is what this project keeps as the source of truth.
    """
    if decision.reason_code != "context_complete":
        return decision
    if not last_asked_for_intent(history):
        return decision
    if not is_bare_answer(message):
        return decision
    return decision.model_copy(
        update={
            "intent": Intent.UNCLEAR,
            "need_clarification": True,
            "reason_code": "still_unclear_after_clarify",
            "missing_slots": [],
        }
    )
