"""A word-list reading of one traveller line, and a session frustration index.

A line scores from -1 to +1. Almost nothing said is left unscored. A negation
flips a marked word and halves it. An intensifier strengthens the next marked
word. A repeated marked word counts in full once, then only a little more.
The session number is the average depth of the negative lines, and it stays
blank until three lines have a score.
"""

from __future__ import annotations

import re
from typing import Any

# A line this short is not a reading. A zero would look like calm.
_MIN_CHARS = 2
# The same band the reference uses for a direction. Inside it, the line is neutral.
_LABEL_AT = 0.15
# A session index needs this many scored lines. Fewer is "not measured", not calm.
_MIN_LINES = 3

_STRONG = frozenset({
    "hate", "useless", "stupid", "idiot", "fuck", "fucking", "shit", "damn", "worst",
    "angry", "ridiculous", "scam", "bullshit", "terrible", "awful", "horrible",
    "rubbish", "garbage", "trash", "crap", "wtf",
    "垃圾", "廢物", "废物", "激氣", "激气",
})
_MILD = frozenset({
    "slow", "wrong", "broken", "confused", "annoying", "bad", "upset", "unfair",
    "waiting", "annoyed", "frustrated", "problem", "waste", "sucks", "suck", "hell",
    "差", "煩", "烦", "衰",
})
_KIND = frozenset({
    "thanks", "thank", "great", "good", "please", "helpful", "love", "lovely", "perfect",
    "好", "謝謝", "谢谢", "唔該",
})
_NOT = frozenset({
    "not", "no", "never", "nt", "dont", "don't", "isnt", "isn't", "wasnt", "wasn't",
    "without", "唔", "冇", "不", "没", "沒",
})
_BOOST = frozenset({"very", "really", "extremely", "totally", "absolutely", "so", "好", "非常"})
_LATIN = re.compile(r"[a-z']+")
_CJK = re.compile(r"[\u4e00-\u9fff]{1,2}")


def _tokens(lower: str) -> list[str]:
    latin = [(match.start(), match.group(0)) for match in _LATIN.finditer(lower)]
    cjk = [(match.start(), match.group(0)) for match in _CJK.finditer(lower)]
    return [token for _, token in sorted(latin + cjk)]


def _weight(token: str) -> float:
    if token in _STRONG:
        return -0.72
    if token in _MILD:
        return -0.4
    if token in _KIND:
        return 0.4
    if len(token) > 1 and token[0] in {"唔", "不", "没", "沒"} and token[1:] in _STRONG | _MILD:
        return 0.0
    return 0.0


def line_reading(text: str) -> dict[str, Any]:
    """One line on -1..+1, or no score when almost nothing was said.

    ``notes`` names which rules fired, so the desk can say so without
    inventing a second explanation.
    """
    raw = (text or "").strip()
    if len(raw) < _MIN_CHARS:
        return {"score": None, "label": "not_measured", "notes": ["unmeasured"]}
    tokens = _tokens(raw.lower())
    if not tokens:
        return {"score": None, "label": "not_measured", "notes": ["unmeasured"]}

    notes: list[str] = []
    total = 0.0
    seen: dict[str, int] = {}
    marked = False
    for index, token in enumerate(tokens):
        base = _weight(token)
        negated_piece = ""
        if base == 0.0 and len(token) > 1 and token[0] in {"唔", "不", "没", "沒"}:
            base = _weight(token[1:])
            negated_piece = token[1:]
        if base == 0.0:
            continue
        marked = True
        key = negated_piece or token
        times = seen.get(key, 0)
        seen[key] = times + 1
        if times == 0:
            weight = base
        elif times <= 2:
            weight = base * 0.15
            if "repeat" not in notes:
                notes.append("repeat")
        else:
            continue
        window = tokens[max(0, index - 3):index]
        if negated_piece or any(item in _NOT for item in window):
            weight = -0.5 * weight
            if "negation" not in notes:
                notes.append("negation")
        if index > 0 and tokens[index - 1] in _BOOST:
            weight *= 1.35
            if "intensifier" not in notes:
                notes.append("intensifier")
        total += weight
        if weight < 0 and "negative" not in notes:
            notes.append("negative")
        elif weight > 0 and "positive" not in notes:
            notes.append("positive")

    if not marked:
        return {"score": 0.0, "label": "neutral", "notes": ["plain"]}

    letters = [ch for ch in raw if ch.isalpha()]
    shouted = len(letters) >= 6 and sum(ch.isupper() for ch in letters) / len(letters) > 0.6
    if total < 0 and (shouted or raw.count("!") >= 2):
        total -= 0.12 if shouted else 0.0
        total -= min(0.2, raw.count("!") * 0.05)
        notes.append("loud")

    score = max(-1.0, min(1.0, total))
    if score >= _LABEL_AT:
        label = "positive"
    elif score <= -_LABEL_AT:
        label = "negative"
    else:
        label = "neutral"
    return {"score": round(score, 2), "label": label, "notes": notes}


def frustration_score(text: str) -> int:
    """How far one line sits below calm, 0 to 100. An unscored line is 0, not a claim that it was calm."""
    score = line_reading(text)["score"]
    if score is None:
        return 0
    return round(max(0.0, -float(score)) * 100)


def frustration_band(score: int) -> str:
    """calm, uneasy, or hot. The cut points are Pathfinder's desk labels."""
    if score >= 60:
        return "hot"
    if score >= 25:
        return "uneasy"
    return "calm"


def session_frustration(scores: list[float]) -> int | None:
    """Average negative depth of a session, 0 to 100.

    ``None`` when fewer than three lines were scored. That absence is not a
    calm session.
    """
    if len(scores) < _MIN_LINES:
        return None
    depth = sum(max(0.0, -score) for score in scores) / len(scores)
    return round(depth * 100)
