"""Read the day sections out of a plan answer that is still arriving.

The composer streams one JSON object. A day is shown once its object closes, and
the day still being written is shown from what is there. Everything here is
best-effort: a half-written string, a missing field, or prose around the object
returns fewer days, never an exception.
"""

from __future__ import annotations

import re
from typing import Any

_DATE = re.compile(r'"date"\s*:\s*"([^"]{4,40})"')
_TITLE = re.compile(r'"title"\s*:\s*"([^"]{0,120})"')
_REF = re.compile(r'"ref"\s*:\s*"([^"]{1,80})"')
_NAME = re.compile(r'"name"\s*:\s*"([^"]{1,120})"')
_START = re.compile(r'"start"\s*:\s*"([^"]{0,10})"')
_END = re.compile(r'"end"\s*:\s*"([^"]{0,10})"')


def _objects(text: str, start: int) -> list[str]:
    """The complete `{...}` objects of an array that begins at or after `start`."""
    array = text.find("[", start)
    if array < 0:
        return []
    out: list[str] = []
    in_string = False
    escaped = False
    opened: int | None = None
    depth = 0
    for index in range(array, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            depth += 1
            if char == "{" and depth == 2:
                opened = index
        elif char in "}]":
            depth -= 1
            if char == "}" and depth == 1 and opened is not None:
                out.append(text[opened:index + 1])
                opened = None
            elif char == "]" and depth == 0:
                if opened is not None:
                    out.append(text[opened:])
                break
    if opened is not None:
        out.append(text[opened:])
    return out


def _stop(chunk: str) -> dict[str, str] | None:
    ref = _REF.search(chunk)
    name = _NAME.search(chunk)
    if not (ref or name):
        return None
    start = _START.search(chunk)
    end = _END.search(chunk)
    return {
        "ref": ref.group(1) if ref else "",
        "name": name.group(1) if name else "",
        "start": start.group(1) if start else "",
        "end": end.group(1) if end else "",
    }


def _day(chunk: str) -> dict[str, Any] | None:
    date = _DATE.search(chunk)
    title = _TITLE.search(chunk)
    stops_at = chunk.find('"stops"')
    stops = [_stop(item) for item in _objects(chunk, stops_at)] if stops_at >= 0 else []
    stops = [stop for stop in stops if stop]
    if not (date or title or stops):
        return None
    return {
        "date": date.group(1) if date else "",
        "title": title.group(1) if title else "",
        "stops": stops[:8],
    }


def partial_days(text: str) -> list[dict[str, Any]]:
    """The day objects written so far, in order; the last one may still be open."""
    start = text.find('"days"')
    if start < 0:
        return []
    days: list[dict[str, Any]] = []
    for chunk in _objects(text, start):
        day = _day(chunk)
        if day:
            days.append(day)
    return days[:31]
