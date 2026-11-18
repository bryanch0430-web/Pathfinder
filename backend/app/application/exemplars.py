"""Word overlap used when a handoff example is compared with a message.

A saved handoff line has to share real words with what the traveller wrote.
One shared little word is not enough.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[a-z0-9']+")
_STOP = frozenset({"a", "an", "the", "to", "of", "and", "or", "for", "in", "on", "my", "me", "please"})


def tokens(text: str) -> set[str]:
    """The words that carry a line, without the little ones every sentence shares."""
    return {word for word in _TOKEN.findall((text or "").lower()) if word not in _STOP and len(word) > 1}


def similarity(left: str, right: str) -> float:
    """How much of the wording two lines share, from 0 to 1."""
    a, b = tokens(left), tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
