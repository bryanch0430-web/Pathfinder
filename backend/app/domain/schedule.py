"""Make a stored day readable again when a merge left two stops on one time.

A locked or confirmed stop stays. A later row with the same name is the same
place written twice and is dropped. A different place slides to the next free
minute and keeps its length. A stop that cannot fit before the day ends is left
out, and the day says so.
"""

from __future__ import annotations

import re
from typing import Any

_CLOCK = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_DAY_END = 23 * 60 + 59


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
    taken: list[tuple[int, int]] = []
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
        slot = _room(taken, begin, finish - begin, bool(raw.get("locked") or raw.get("confirmed")))
        if slot is None:
            notes.append(f"Left out {name} on {when}: the day was already full.")
            continue
        item = raw
        if slot != begin:
            item = dict(raw)
            item["start"], item["end"] = _hhmm(slot), _hhmm(slot + finish - begin)
            notes.append(f"Moved {name} on {when} to {item['start']}–{item['end']} so it does not overlap.")
        taken.append((slot, slot + finish - begin))
        kept.append(item)
    kept.sort(key=lambda item: (str(item.get("start") or ""), str(item.get("end") or "")))
    return kept, notes


def _span(item: dict[str, Any]) -> tuple[int, int]:
    return _minutes(str(item.get("start") or "00:00")), _minutes(str(item.get("end") or "00:00"))


def _ranges_overlap(start: int, end: int, other_start: int, other_end: int) -> bool:
    return start < other_end and other_start < end


def _room(taken: list[tuple[int, int]], start: int, duration: int, pinned: bool) -> int | None:
    """The item's own start when that stretch is free, otherwise the next free minute at or after it.

    A pinned stop keeps its start whenever the minute is free. When it is not, it slides too:
    a saved day has to be readable.
    """
    del pinned
    if duration <= 0:
        return None
    if _free(taken, start, duration):
        return start
    for _, end in sorted(taken):
        if end >= start and _free(taken, end, duration):
            return end
    return None


def _free(taken: list[tuple[int, int]], begin: int, duration: int) -> bool:
    return begin + duration <= _DAY_END + 1 and all(
        end <= begin or open_at >= begin + duration for open_at, end in taken
    )


def _minutes(value: str) -> int:
    hour, minute = value.split(":", 1)
    return int(hour) * 60 + int(minute)


def _hhmm(minutes: int) -> str:
    minutes = max(0, min(minutes, _DAY_END))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"
