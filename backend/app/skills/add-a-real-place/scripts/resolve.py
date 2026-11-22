"""Turn an add or a delete into a day and a search phrase.

The phrase is whatever is left after the scaffolding is removed: the day, the
polite words, and the words that only mean "a place". It is not a list of
sights. The place search, which returns a name and a map point, decides which
real place that phrase is.

    python scripts/resolve.py "add a stop on day 1 where I can see the night view"
"""

from __future__ import annotations

import json
import re
import sys

# Closed-class words and the words that only mean "put a place on a day".
# A sight ("sakura", "pandas", "night view") is never listed here.
_SCAFFOLD = frozenset(
    """
    a an the some any something anything smth just like or and but to of for at on in
    from with where when how about ok okay sorry please hey hi so well um can could
    would you i we me my our wanna want wanted needs need somewhere spot spots stop
    stops place places attraction attractions attrction atracton atrction atrraction
    day days morning afternoon evening see watch look looking try visit go going get have has it this that them
    thats that's that's an add include insert put stick slot throw squeeze find me us
    please pls kinda sort thing things good whatever is are be am
    """.split()
)
_ADD = re.compile(
    r"\b(?:add|include|insert|put|stick|slot|throw|squeeze|find)\b",
    re.IGNORECASE,
)
# A suggestion of a place, with no verb from the line above: "how about a garden on day 2".
_SUGGEST = re.compile(
    r"\b(?:how\s+about|what\s+about|let'?s|i'?d\s+like|we\s+should)\b|\bsomewhere\b",
    re.IGNORECASE,
)
_REMOVE = re.compile(r"\b(?:remove|delete|drop|take\s+out)\b", re.IGNORECASE)
_NOT = re.compile(r"\b(?:don'?t|do\s+not)\s+(?:add|include|put)\b", re.IGNORECASE)
_DAY = re.compile(r"\bday\s*(\d{1,2})\b", re.IGNORECASE)
_ORDINAL = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s+day\b", re.IGNORECASE)
_WORD_DAY = {"first": 1, "second": 2, "third": 3}


def resolve(message: str) -> dict:
    """``{"add": bool, "remove": bool, "day": int | None, "query": str | None}``."""
    text = re.sub(r"\bd\s*a\s*y\b", "day", message or "", flags=re.IGNORECASE)
    day = _day(text)
    removed = _REMOVE.search(text) is not None and _ADD.search(text) is None
    suggested = _SUGGEST.search(text) is not None
    added = (_ADD.search(text) is not None or suggested) and not removed and _NOT.search(text) is None
    query = _query(text) if added else None
    return {"add": bool(added and query), "remove": removed, "day": day, "query": query}


def _day(text: str) -> int | None:
    match = _DAY.search(text) or _ORDINAL.search(text)
    if match:
        return int(match.group(1))
    for word, number in _WORD_DAY.items():
        if re.search(rf"\b{word}\s+day\b", text, re.IGNORECASE):
            return number
    return None


def _query(text: str) -> str | None:
    stripped = _DAY.sub(" ", text)
    stripped = _ORDINAL.sub(" ", stripped)
    words = [
        word
        for word in re.findall(r"[A-Za-z0-9']+", stripped)
        if word.casefold() not in _SCAFFOLD
    ]
    phrase = " ".join(words).strip()
    return phrase or None


def main() -> None:
    message = " ".join(sys.argv[1:])
    print(json.dumps(resolve(message), ensure_ascii=False))


if __name__ == "__main__":
    main()
