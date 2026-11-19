"""Visit length for a stop: the pace slot, unless the shared table requires more or less.

The traveller's own stated length wins. A stop the table does not classify keeps the pace
slot; the review model is only for that remainder, and this module does not call it.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

_TABLE = Path(__file__).resolve().parents[1] / "skills" / "review-duration" / "minima.json"
STATED = "stated-duration"


@cache
def _kinds() -> dict[str, dict]:
    return json.loads(_TABLE.read_text(encoding="utf-8"))


def kind_of(name: str, source_types: list[str] | None = None) -> str | None:
    """The table row for this stop, or None when the review model would be asked."""
    folded = " ".join((name or "").casefold().split())
    types = {item.casefold().replace(" ", "_") for item in (source_types or [])}
    for kind, row in _kinds().items():
        names = {item.casefold() for item in row.get("names") or []}
        labels = {item.casefold() for item in row.get("types") or []}
        if any(needle and needle in folded for needle in names) or types & labels:
            return kind
    return None


def visit_minutes(
    name: str,
    source_types: list[str] | None,
    pace_minutes: int,
    *,
    stated: int | None = None,
) -> int:
    """Minutes to schedule. ``stated`` is a length the traveller asked for, and it wins."""
    if stated is not None and stated > 0:
        return stated
    kind = kind_of(name, source_types)
    if kind is None:
        return pace_minutes
    row = _kinds()[kind]
    minutes = pace_minutes
    if row.get("min"):
        minutes = max(minutes, int(row["min"]))
    if row.get("max"):
        minutes = min(minutes, int(row["max"]))
    return minutes


def unclassified(name: str, source_types: list[str] | None = None) -> bool:
    return kind_of(name, source_types) is None
