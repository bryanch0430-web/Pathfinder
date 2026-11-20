"""When a turn is handed to a person instead of planned.

This is a contact-desk policy, not a fifth planning intent. The router still
chooses plan, change, ask or unclear. A separate check, run before that work,
decides whether this turn should wait for a person.

Three signals, in order, and only while the policy is on:

1. The traveller asked for a person. A short product rule, so it works before
   anyone has saved an example.
2. The chosen match. ``keywords`` hands over on a negative word or on an extra
   example used as a keyword. ``examples`` hands over when a saved line is a
   close match, then on a frustration number if one is set.
3. A frustration threshold, in ``examples`` mode only. Left unset, heat alone never escalates.
"""

from __future__ import annotations

import re
from typing import Any

from app.application.exemplars import similarity, tokens
from app.application.sentiment import keyword_hit

# A saved example has to be this close, and share two content words.
_EXAMPLE_BAR = 0.6
_ASKS_FOR_A_PERSON = re.compile(
    r"\b(live agent|human agent|real person|customer service|"
    r"(?:talk|speak) to (?:a |an )?(?:human|person|agent|someone|somebody)|"
    r"get me (?:a |an )?(?:human|person|agent)|"
    r"find (?:someone|somebody) to help|"
    r"(?:need|want) (?:a |someone|somebody) to help|"
    r"escalate)\b|"
    r"转人工|轉人工|人工客服|真人客服",
    re.IGNORECASE,
)


_NEGATED_ESCALATION = re.compile(
    r"\b(?:do\s+not|don['’]?t|dont|never|no\s+need\s+to|without)\s+(?:(?:\w+\s+){0,2})?"
    r"(?:talk|speak|get\s+me|escalate|transfer)\b",
    re.IGNORECASE,
)


# Replies that mean the edit changed nothing. A successful one-row edit says
# "everything else is unchanged", which is not one of these.
_FAIL = (
    "stayed as it was",
    "left the plan as it was",
    "left the plan as it is",
    "nothing in the plan matched",
    "the plan is unchanged",
    "itinerary is unchanged",
    "nothing was changed",
    "nothing else was changed",
)
FAIL_PHRASES = _FAIL

# Shown on the escalate card. The pattern above still matches these if the card is cleared.
LIVE_AGENT_PHRASES = (
    "talk to a live agent",
    "find someone to help",
    "human agent",
    "real person",
    "customer service",
    "转人工",
    "轉人工",
    "人工客服",
    "真人客服",
)


def should_escalate(message: str, frustration: int, policy: dict[str, Any]) -> str | None:
    """Why this turn should be handed over, or None when planning should continue."""
    parts = policy.get("parts")
    if isinstance(parts, dict) and isinstance(parts.get("hand"), dict):
        return hand_reason(message, frustration, parts["hand"])
    if not policy.get("enabled", True):
        return None
    if _ASKS_FOR_A_PERSON.search(message or "") and not _NEGATED_ESCALATION.search(message or ""):
        return "asked_for_a_person"
    examples = policy.get("examples") or []
    if str(policy.get("match_mode") or "examples") == "keywords":
        return keyword_hit(message, [_utterance(item) for item in examples], list(policy.get("words") or []))
    for example in examples:
        utterance = _utterance(example)
        if not utterance.strip():
            continue
        shared = tokens(message) & tokens(utterance)
        if len(shared) >= 2 and similarity(message, utterance) >= _EXAMPLE_BAR:
            return "example"
    limit = policy.get("frustration_at")
    if isinstance(limit, (int, float)) and not isinstance(limit, bool) and frustration >= limit:
        return "frustration"
    return None


def _utterance(example: Any) -> str:
    return str(example.get("utterance") or "") if isinstance(example, dict) else str(example)


def _failed_edit(message: Any, content: str) -> bool:
    """An assistant reply that applied no change.

    The stored edit is the sure sign: an empty diff and a log that says it failed.
    The phrases cover the same replies when that record is not on the message.
    """
    meta = getattr(message, "meta", None)
    if isinstance(meta, dict):
        diff = meta.get("diff")
        log = meta.get("log")
        if isinstance(diff, list) and not diff and isinstance(log, dict) and log.get("ok") is False:
            return True
    folded = content.casefold()
    return any(phrase in folded for phrase in _FAIL)


def _utterances(value: Any) -> list[str]:
    rows: list[str] = []
    for item in value or []:
        text = item.get("utterance") if isinstance(item, dict) else item
        utterance = str(text or "").strip()
        if utterance:
            rows.append(utterance)
    return rows


def _close(text: str, examples: list[str]) -> bool:
    for utterance in examples:
        shared = tokens(text) & tokens(utterance)
        if len(shared) >= 2 and similarity(text, utterance) >= _EXAMPLE_BAR:
            return True
    return False


def _scored_rows(value: Any) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    for item in value or []:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("utterance") or "").strip()
            score = item.get("score", 60)
        else:
            text = str(item).strip()
            score = 60
        if not text:
            continue
        try:
            number = int(score)
        except (TypeError, ValueError):
            number = 60
        rows.append((text, max(0, min(100, number))))
    return rows


def best_match(text: str, words: Any, examples: Any) -> int | None:
    """The highest score of a keyword or example found in the line. None when nothing matches."""
    folded = (text or "").casefold()
    if not folded.strip():
        return None
    best: int | None = None
    for needle, score in _scored_rows(words) + _scored_rows(examples):
        if len(needle) >= 2 and needle.casefold() in folded and (best is None or score > best):
            best = score
    return best


def _bar(part: dict[str, Any]) -> int:
    limit = part.get("score")
    if isinstance(limit, bool) or not isinstance(limit, (int, float)):
        return 60
    return int(limit)


def line_is_hot(text: str, frustration: int, part: dict[str, Any], *, jev_scored: bool = False) -> bool:
    """Whether this part's own engine calls the line hot."""
    from app.application.sentiment import is_distress

    if not part.get("enabled", False):
        return False
    engine = str(part.get("engine") or "keywords")
    bar = _bar(part)
    if engine == "jev" and jev_scored:
        return frustration >= bar or is_distress(text)
    found = best_match(text, part.get("words"), part.get("examples"))
    return (found is not None and found >= bar) or is_distress(text)


def hand_reason(message: str, frustration: int, hand: dict[str, Any]) -> str | None:
    """Why this part hands the chat to a person, using only its own lists and engine."""
    if not hand.get("enabled", True):
        return None
    if _ASKS_FOR_A_PERSON.search(message or "") and not _NEGATED_ESCALATION.search(message or ""):
        return "asked_for_a_person"
    del frustration
    if best_match(message, hand.get("words"), hand.get("examples")) is None:
        return None
    return "negative_word"


_EDIT = re.compile(
    r"\b(?:change|swap|replace|switch)\b.{0,48}\b(?:something else|another|different|instead|this|it|that)\b"
    r"|\bsomething else\b"
    r"|\b(?:don'?t|do not) want to (?:eat|have|go|see|do)\b"
    r"|\b(?:don'?t|do not) want (?:this|that|it)\b",
    re.IGNORECASE | re.DOTALL,
)


def keeps_the_planner(message: str) -> bool:
    """A request to change the plan stays with the planner, even when the wording is unhappy.

    Asking for a person, or a line of distress, is not one of these.
    """
    text = message or ""
    if _ASKS_FOR_A_PERSON.search(text) and not _NEGATED_ESCALATION.search(text):
        return False
    from app.application.sentiment import is_distress

    if is_distress(text):
        return False
    return _EDIT.search(text) is not None


def queue_reason(message: str, *, hot: bool, stuck: bool) -> str | None:
    """Why a hot or stuck line should wait for a person. A plan change is not that reason."""
    if keeps_the_planner(message):
        hot = False
    return agent_reason(100 if hot else 0, stuck)


def agent_reason(frustration: int, stuck: bool) -> str | None:
    """Why a hot or stuck chat should wait in the queue. None when the planner keeps it.

    Hot is a line at 60 or more, the same mark the desk prints. Stuck is the repeated failed edit.
    """
    if frustration >= 60:
        return "hot"
    if stuck:
        return "stuck"
    return None


def _reply_failed(
    reply: Any,
    content: str,
    *,
    engine: str,
    phrases: list[str],
    examples: list[str],
    user: Any,
    bar: int,
) -> bool:
    if engine == "jev":
        heat = getattr(user, "meta", None)
        score = heat.get("stuck_heat") if isinstance(heat, dict) else None
        if isinstance(score, (int, float)) and not isinstance(score, bool) and score >= bar:
            return True
    found = best_match(content, phrases, examples)
    if phrases or examples:
        return found is not None and found >= bar
    return _failed_edit(reply, content)


def is_stuck(
    messages: list[Any],
    after: int = 3,
    *,
    phrases: list[str] | None = None,
    examples: list[str] | None = None,
    engine: str = "phrases",
    bar: int = 60,
) -> bool:
    """True when the same words keep coming back on edits that left the plan unchanged.

    ``after`` is how many of those failed edits must share one content word.
    ``phrases`` and ``examples`` belong to the stuck part. Without them, the built-in reply check is used.
    """
    need = max(2, int(after))
    failed: list[str] = []
    previous_user = ""
    previous_message: Any = None
    own_phrases = phrases is not None
    for message in messages:
        role = getattr(message, "role", "")
        content = str(getattr(message, "content", "") or "")
        if role == "user":
            previous_user = content
            previous_message = message
            continue
        if role != "assistant" or not previous_user:
            continue
        failed_reply = (
            _reply_failed(
                message,
                content,
                engine=engine,
                phrases=list(phrases or []),
                examples=list(examples or []),
                user=previous_message,
                bar=bar,
            )
            if own_phrases
            else _failed_edit(message, content)
        )
        if failed_reply:
            failed.append(previous_user)
    if len(failed) < need:
        return False
    sets = [tokens(text) for text in failed[-need:]]
    shared = set.intersection(*sets) if sets else set()
    return any(len(word) >= 4 for word in shared)
