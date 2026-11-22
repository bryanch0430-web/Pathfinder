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
# A link is not a sentence. "trip" inside hk.trip.com is not the word trip.
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)


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
    "no itinerary items needed to change",
    "was not added",
    "was left out of",
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


def _without_urls(message: str) -> str:
    return _URL.sub(" ", message or "")


def _phrase_hit(text: str, needle: str) -> bool:
    """A whole phrase, not a piece of a longer word and not a piece of a link.

    A latin word has to be at least four letters. "hell" does not match "hello", and "bad" does not match "badge".
    """
    needle = needle.strip()
    if not needle:
        return False
    if needle.isascii() and len(needle) < 4:
        return False
    if not needle.isascii():
        return needle.casefold() in text.casefold()
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", text, re.IGNORECASE) is not None


def _listed_phrase(text: str, words: Any, examples: Any) -> bool:
    for needle, _score in _scored_rows(words) + _scored_rows(examples):
        if _phrase_hit(text, needle):
            return True
    return False


def hand_reason(message: str, frustration: int, hand: dict[str, Any]) -> str | None:
    """Why this part hands the chat to a person, using only its own lists and engine.

    Asking for a person is the tight keyword and it stays on when the extra word list is off.
    The extra list has to be a whole phrase. A link is ignored, so a URL cannot contain the phrase.
    """
    spoken = _without_urls(message)
    if _ASKS_FOR_A_PERSON.search(spoken) and not _NEGATED_ESCALATION.search(spoken):
        return "asked_for_a_person"
    if not hand.get("enabled", True):
        return None
    del frustration
    if not _listed_phrase(spoken, hand.get("words"), hand.get("examples")):
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
    from app.application.understanding import is_clarify_answer

    if is_clarify_answer(message):
        # The traveller is answering the form the app just showed. Earlier failed edits do not apply.
        return None
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


#: Words a repeated question shares by grammar, not by meaning ("Which stop...?", "Which day...?"). Only a
#: shared word outside this set marks the same question asked again.
_QUESTION_FILLER = frozenset(
    "which what where when who whom whose how why would could should shall will can may might do does did "
    "is are was were you your yours i me my we us our they them their it its this that these those there "
    "here a an the to of in on at for with and or not no yes ok okay please tell know let add change make "
    "like want need prefer one ones any some".split()
)


def _question_runs(turns: list[tuple[str, Any, Any, str]], need: int) -> list[int]:
    """The turn indices of every run of ``need`` consecutive assistant questions that share a content word.

    "Which sushi restaurant would you like to add?" asked three times is a loop the plan never leaves,
    however it is rephrased; two different clarifying questions ("Where to?", "When?") share no content
    word and are a normal chat.
    """
    asked: list[tuple[int, set[str]]] = []
    for index, (_, _, _, content) in enumerate(turns):
        if "?" in content:
            words = {word for word in tokens(content) if word not in _QUESTION_FILLER and len(word) >= 4}
            asked.append((index, words))
    stuck: list[int] = []
    start = 0
    for position in range(1, len(asked) + 1):
        same = (
            position < len(asked)
            and asked[position][0] == asked[position - 1][0] + 1
            and bool(asked[position][1] & asked[position - 1][1])
        )
        if same:
            continue
        if position - start >= need:
            stuck.extend(asked[j][0] for j in range(start, position))
        start = position
    return stuck


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

    ``after`` is how many of those failed edits must share one content word. A run of replies that ask the
    same question again also counts: the plan did not move, however the question is phrased.
    ``phrases`` and ``examples`` belong to the stuck part. Without them, the built-in reply check is used.
    """
    from app.application.understanding import is_clarify_answer

    need = max(2, int(after))
    turns: list[tuple[str, Any, Any, str]] = []  # user text, user message, assistant message, assistant text
    previous_user = ""
    previous_message: Any = None
    for message in messages:
        role = getattr(message, "role", "")
        content = str(getattr(message, "content", "") or "")
        if role == "user":
            previous_user = content
            previous_message = message
            continue
        if role != "assistant" or not previous_user:
            continue
        # Filling the dates form is progress. Those lines share "missing details" and must not count as a loop.
        if is_clarify_answer(previous_user):
            continue
        turns.append((previous_user, previous_message, message, content))
    failed: list[str] = []
    counted: set[int] = set()
    own_phrases = phrases is not None
    for index, (user_text, user_message, assistant_message, content) in enumerate(turns):
        failed_reply = (
            _reply_failed(
                assistant_message,
                content,
                engine=engine,
                phrases=list(phrases or []),
                examples=list(examples or []),
                user=user_message,
                bar=bar,
            )
            if own_phrases
            else _failed_edit(assistant_message, content)
        )
        if failed_reply:
            failed.append(user_text)
            counted.add(index)
    failed.extend(turns[index][0] for index in _question_runs(turns, need) if index not in counted)
    if len(failed) < need:
        return False
    sets = [tokens(text) for text in failed[-need:]]
    shared = set.intersection(*sets) if sets else set()
    return any(len(word) >= 4 for word in shared)


def stuck_flag(messages: list[Any], reply: str) -> int:
    """How many times this failed reply has repeated the same request, counting this one.

    0 when the reply is not a failed edit. 1 is the first failure, 2 the next, and so on.
    """
    if not _failed_edit(None, reply):
        return 0
    request = ""
    for message in reversed(messages):
        if getattr(message, "role", "") == "user":
            request = str(getattr(message, "content", "") or "")
            break
    if not request:
        return 1
    count = 1
    previous = ""
    pairs: list[tuple[str, str]] = []
    for message in messages:
        role = getattr(message, "role", "")
        content = str(getattr(message, "content", "") or "")
        if role == "user":
            previous = content
        elif role == "assistant" and previous:
            pairs.append((previous, content))
    asked = tokens(request)
    for user_text, content in reversed(pairs):
        if not _failed_edit(None, content):
            break
        shared = tokens(user_text) & asked
        if not any(len(word) >= 4 for word in shared):
            break
        count += 1
    return count
