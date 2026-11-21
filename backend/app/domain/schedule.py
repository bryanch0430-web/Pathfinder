"""Make a stored day readable again when the same place was written twice.

A later row with the same name on an overlapping clock is the same place and is
dropped. A different place keeps its clock, even when the times cross: the page
shows that overlap as a warning.
"""

from __future__ import annotations

import re
from typing import Any

_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def record_overlaps(data: dict[str, Any]) -> list[str]:
    """Append one line for each shared clock. A line already recorded is left as it is.

    Fixing a time does not delete the old line. The list only grows, up to 80 lines.
    """
    quality = data.get("data_quality")
    if not isinstance(quality, dict):
        quality = {}
        data["data_quality"] = quality
    notes = [str(line) for line in (quality.get("notes") or []) if str(line).strip()]
    fresh: list[str] = []
    for day in data.get("days") or []:
        if not isinstance(day, dict):
            continue
        when = str(day.get("date") or "")
        ordered = sorted(
            (item for item in (day.get("items") or []) if isinstance(item, dict)),
            key=lambda item: (str(item.get("start") or ""), str(item.get("end") or "")),
        )
        for index, raw in enumerate(ordered):
            start, end = str(raw.get("start") or ""), str(raw.get("end") or "")
            if _CLOCK.match(start) is None or _CLOCK.match(end) is None or start >= end:
                continue
            finish = _minutes(end)
            name = str(raw.get("name") or "A stop")
            for other in ordered[index + 1 :]:
                other_start, other_end = str(other.get("start") or ""), str(other.get("end") or "")
                if _CLOCK.match(other_start) is None or _CLOCK.match(other_end) is None:
                    continue
                if _minutes(other_start) >= finish:
                    break
                other_name = str(other.get("name") or "A stop")
                if other_name.casefold() == name.casefold():
                    continue
                line = f"{name} and {other_name} overlap on {when}."
                if line not in notes and line not in fresh:
                    fresh.append(line)
    if fresh:
        quality["notes"] = (notes + fresh)[-80:]
    return fresh


def separate_overlaps(data: dict[str, Any]) -> list[str]:
    """Rewrite ``data`` in place. Returns one short note per change."""
    notes: list[str] = []
    for day in data.get("days") or []:
        if not isinstance(day, dict):
            continue
        items = [item for item in (day.get("items") or []) if isinstance(item, dict)]
        kept, day_notes = _separate_day(str(day.get("date") or ""), items)
        day["items"] = kept
        notes.extend(day_notes)
    if notes:
        quality = data.get("data_quality")
        if isinstance(quality, dict):
            existing = [str(line) for line in (quality.get("notes") or []) if str(line).strip()]
            quality["notes"] = list(dict.fromkeys(existing + notes))[:30]
    return notes


def _separate_day(when: str, items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    ordered = sorted(items, key=lambda item: (str(item.get("start") or ""), str(item.get("end") or "")))
    kept: list[dict[str, Any]] = []
    notes: list[str] = []
    for raw in ordered:
        start, end = str(raw.get("start") or ""), str(raw.get("end") or "")
        if _CLOCK.match(start) is None or _CLOCK.match(end) is None or start >= end:
            kept.append(raw)
            continue
        name = str(raw.get("name") or "A stop")
        begin, finish = _minutes(start), _minutes(end)
        twin = next(
            (
                earlier
                for earlier in kept
                if name.casefold() == str(earlier.get("name") or "").casefold()
                and _ranges_overlap(begin, finish, *_span(earlier))
            ),
            None,
        )
        if twin is not None:
            notes.append(f"Merged a duplicate {name} on {when}.")
            continue
        kept.append(raw)
    kept.sort(key=lambda item: (str(item.get("start") or ""), str(item.get("end") or "")))
    return kept, notes


def _span(item: dict[str, Any]) -> tuple[int, int]:
    return _minutes(str(item.get("start") or "00:00")), _minutes(str(item.get("end") or "00:00"))


def _ranges_overlap(start: int, end: int, other_start: int, other_end: int) -> bool:
    return start < other_end and other_start < end


def _minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)
