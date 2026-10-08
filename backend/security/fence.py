"""Data fencing: untrusted text is embedded in prompts only inside a labelled data block.

Proposal: "The console treats the user message as trip data, not as an instruction." The system
prompt tells the model that anything inside `<untrusted_data ...>` is data to extract trip facts
from. That only holds if the user cannot close the block themselves, so every spelling of the
fence tag inside the text is defused before wrapping (case-insensitive, with optional whitespace
or zero-width characters inside the tag, and the full-width `<`).
"""

from __future__ import annotations

import re

FENCE_OPEN = "<untrusted_data"
FENCE_CLOSE = "</untrusted_data>"

_LABEL_RE = re.compile(r"[a-z_]+")

# Characters that render as nothing; an attacker can sprinkle them inside a tag name.
_INVISIBLE = "\\s\u00ad\u034f\u061c\u180b-\u180e\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060-\u206f\ufe00-\ufe0f\ufeff"
_GAP = f"[{_INVISIBLE}]*"

# "<untrusted_data" or "</untrusted_data" in any case, with invisible characters or whitespace
# anywhere between the pieces, starting with an ASCII or full-width "<".
_FENCE_TAG_RE = re.compile(
    "[<\uff1c]" + _GAP + "/?" + _GAP + _GAP.join(re.escape(c) for c in "untrusted_data"),
    re.IGNORECASE,
)

# Replacement for the opening "<" of a defused tag: SINGLE LEFT-POINTING ANGLE QUOTATION MARK.
_DEFUSED_LT = "\u2039"

_BLOCK_RE = re.compile(
    re.escape(FENCE_OPEN) + r' label="([a-z_]+)">\n(.*)\n' + re.escape(FENCE_CLOSE),
    re.DOTALL,
)


def _defuse(text: str) -> str:
    return _FENCE_TAG_RE.sub(lambda m: _DEFUSED_LT + m.group(0)[1:], text)


def fence(text: str, *, label: str) -> str:
    """Wrap `text` as `<untrusted_data label="...">\\n...\\n</untrusted_data>`. Any occurrence
    of the fence tags inside `text` (any case) is neutralised so the text cannot close the block
    early. `label` must match [a-z_]+."""
    if not isinstance(label, str) or not _LABEL_RE.fullmatch(label):
        raise ValueError("fence label must match [a-z_]+")
    return f'{FENCE_OPEN} label="{label}">\n{_defuse(text)}\n{FENCE_CLOSE}'


def unfence(fenced: str) -> str:
    """Inverse of fence() for a single block (used by the mock model and tests). Raises
    ValueError when `fenced` is not exactly one fenced block."""
    match = _BLOCK_RE.fullmatch(fenced.strip())
    if match is None:
        raise ValueError("not a single untrusted_data block")
    inner = match.group(2)
    if _FENCE_TAG_RE.search(inner):
        raise ValueError("not a single untrusted_data block")
    return inner
