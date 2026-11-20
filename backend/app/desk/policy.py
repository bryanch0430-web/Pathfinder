"""A person can hold a chat before any planner node runs.

Asking for a person queues the chat. The same failed edit, repeated, does too.
Sentiment is not a reason. A held chat never enters the graph.
"""

from __future__ import annotations

import re
from typing import Any

ASKS_FOR_A_PERSON = re.compile(
    r"\b(live agent|human agent|real person|customer service|"
    r"(?:talk|speak) to (?:a |an )?(?:human|person|agent|someone|somebody)|"
    r"get me (?:a |an )?(?:human|person|agent)|"
    r"find (?:someone|somebody) to help|"
    r"(?:need|want) (?:a |someone|somebody) to help|"
    r"escalate)\b|"
    r"转人工|轉人工|人工客服|真人客服",
    re.IGNORECASE,
)
NEGATED = re.compile(
    r"\b(?:do\s+not|don['’]?t|dont|never|no\s+need\s+to|without)\s+(?:(?:\w+\s+){0,2})?"
    r"(?:talk|speak|get\s+me|escalate|transfer)\b",
    re.IGNORECASE,
)
HANDOFF_TEXT = (
    "A person will take this chat from here. The planner will not answer this turn."
)
HELD_TEXT = "A person has this chat and will reply here."
STUCK_AFTER = 3
_WORD = re.compile(r"[a-z0-9]{4,}", re.I)


def asks_for_a_person(message: str) -> bool:
    text = message or ""
    if NEGATED.search(text):
        return False
    return ASKS_FOR_A_PERSON.search(text) is not None


def desk_state(preferences: dict | None) -> dict[str, Any]:
    raw = (preferences or {}).get("desk")
    if not isinstance(raw, dict):
        raw = {}
    status = raw.get("status") if raw.get("status") in {"auto", "queued", "handling"} else "auto"
    return {
        "status": status,
        "reason": raw.get("reason") or "",
        "stuck": int(raw.get("stuck") or 0),
        "last_words": list(raw.get("last_words") or []),
    }


def queue(desk: dict, reason: str) -> dict:
    return {**desk, "status": "queued", "reason": reason}


def hold(desk: dict) -> dict:
    return {**desk, "status": "handling", "reason": desk.get("reason") or "takeover"}


def release(desk: dict) -> dict:
    return {"status": "auto", "reason": "", "stuck": 0, "last_words": []}


def content_words(message: str) -> set[str]:
    return {word.casefold() for word in _WORD.findall(message or "")}


def note_unchanged_edit(desk: dict, message: str, *, unchanged: bool, after: int = STUCK_AFTER) -> dict:
    """Count an edit that left the plan as it was, when it shares a word with the last one."""
    if not unchanged:
        return {**desk, "stuck": 0, "last_words": []}
    words = content_words(message)
    previous = set(desk.get("last_words") or [])
    count = int(desk.get("stuck") or 0)
    count = count + 1 if not previous or words & previous else 1
    updated = {**desk, "stuck": count, "last_words": sorted(words)}
    if count >= max(2, int(after)):
        return queue(updated, "stuck")
    return updated
