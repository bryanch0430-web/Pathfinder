"""When a turn is handed to a person instead of planned.

This is a contact-desk policy, not a fifth planning intent. The router still
chooses plan, change, ask or unclear. A separate check, run before that work,
decides whether this turn should wait for a person.

Three signals, in order, and only while the policy is on:

1. The traveller asked for a person. A short product rule, so it works before
   anyone has saved an example.
2. A saved example is close. The same overlap test as a training example, and
   a higher bar, so one short line does not catch every nearby sentence.
3. A frustration threshold the admin set. Left unset, heat alone never escalates.
"""

from __future__ import annotations

import re
from typing import Any

from app.application.exemplars import similarity, tokens

# A saved example has to be this close, and share two content words.
_EXAMPLE_BAR = 0.6
_ASKS_FOR_A_PERSON = re.compile(
    r"\b(live agent|human agent|real person|customer service|"
    r"(?:talk|speak) to (?:a |an )?(?:human|person|agent|someone|somebody)|"
    r"get me (?:a |an )?(?:human|person|agent)|escalate)\b|"
    r"转人工|轉人工|人工客服|真人客服",
    re.IGNORECASE,
)


_NEGATED_ESCALATION = re.compile(
    r"\b(?:do\s+not|don['’]?t|dont|never|no\s+need\s+to|without)\s+(?:(?:\w+\s+){0,2})?"
    r"(?:talk|speak|get\s+me|escalate|transfer)\b",
    re.IGNORECASE,
)


def should_escalate(message: str, frustration: int, policy: dict[str, Any]) -> str | None:
    """Why this turn should be handed over, or None when planning should continue."""
    if not policy.get("enabled", True):
        return None
    if _ASKS_FOR_A_PERSON.search(message or "") and not _NEGATED_ESCALATION.search(message or ""):
        return "asked_for_a_person"
    for example in policy.get("examples") or []:
        utterance = str(example.get("utterance") or "") if isinstance(example, dict) else str(example)
        if not utterance.strip():
            continue
        shared = tokens(message) & tokens(utterance)
        if len(shared) >= 2 and similarity(message, utterance) >= _EXAMPLE_BAR:
            return "example"
    limit = policy.get("frustration_at")
    if isinstance(limit, (int, float)) and not isinstance(limit, bool) and frustration >= limit:
        return "frustration"
    return None
