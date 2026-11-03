"""Opening hours as the place sources state them.

The sources write hours in three shapes:

- Google Places: one line per weekday, "Monday: 9:00 AM – 5:00 PM", "Tuesday: Closed",
  "Wednesday: Open 24 hours", "Thursday: 11:30 AM – 2:30 PM, 5:30 – 10:00 PM".
- OpenStreetMap ``opening_hours``: "Mo-Fr 09:00-17:00; Sa 10:00-14:00; Su off", "24/7".
- Guides and the sandbox: a plain span such as "09:00-17:00" or "9AM-5PM daily".

The planner schedules a stop inside the windows read here. Text it cannot read is
kept on the plan item as stated, but is never turned into a window: an unreadable
line means "not known for scheduling", not "open all day" or "closed".
"""

from __future__ import annotations

import re
from datetime import date

DAY_MINUTES = 24 * 60
Window = tuple[int, int]

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_OSM_DAYS = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")
_WEEKDAY_LINE = re.compile(rf"^\s*({'|'.join(_WEEKDAYS)})\s*:\s*(.*)$", re.IGNORECASE)
_SPAN = re.compile(
    r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*-\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$",
    re.IGNORECASE,
)
_ALL_DAY = re.compile(r"^(?:open\s+)?24\s*(?:hours|hrs|h|/7)$|^24/7$", re.IGNORECASE)
_CLOSED = re.compile(r"^(?:closed|off)$", re.IGNORECASE)
_OSM_DAY_TOKEN = re.compile(r"^(Mo|Tu|We|Th|Fr|Sa|Su|PH|SH)(?:-(Mo|Tu|We|Th|Fr|Sa|Su))?$")
_DAILY_WORDS = re.compile(r"\b(?:daily|every\s+day|open)\b", re.IGNORECASE)


def _normalise(text: str) -> str:
    text = str(text)
    for char in (" ", " ", " ", " "):
        text = text.replace(char, " ")
    for char in ("–", "—", "−", "‒"):
        text = text.replace(char, "-")
    text = re.sub(r"\s*-\s*", "-", text)
    return re.sub(r"\s+", " ", text).strip()


def _clock(hour: str, minute: str | None, meridiem: str | None) -> int | None:
    h, m = int(hour), int(minute or 0)
    if m > 59:
        return None
    if meridiem:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if meridiem.lower() == "pm" else 0)
    elif h > 24:
        return None
    return h * 60 + m


def _span(text: str) -> Window | None:
    match = _SPAN.match(text.strip())
    if not match:
        return None
    h1, m1, mer1, h2, m2, mer2 = match.groups()
    end = _clock(h2, m2, mer2)
    if end is None:
        return None
    if mer1 is None and mer2 is not None:
        # "1:00 - 5:00 PM": the start shares the end's half of the day, unless
        # that would put it after the end ("11:00 - 2:00 PM" is 11 AM).
        start = _clock(h1, m1, mer2)
        if start is not None and start > end:
            start = _clock(h1, m1, "am" if mer2.lower() == "pm" else "pm")
    else:
        start = _clock(h1, m1, mer1)
    if start is None:
        return None
    if end <= start:
        end = DAY_MINUTES  # past midnight: open for the rest of this day
    return start, min(end, DAY_MINUTES)


def _spans(text: str) -> list[Window] | None:
    """Windows in one day's text, [] when closed, None when unreadable."""
    text = _normalise(text)
    if not text:
        return None
    if _CLOSED.match(text):
        return []
    if _ALL_DAY.match(text):
        return [(0, DAY_MINUTES)]
    out: list[Window] = []
    for part in text.split(","):
        window = _span(part)
        if window is None:
            return None
        out.append(window)
    return sorted(out)


def _osm_days(selector: str) -> set[int] | None:
    days: set[int] = set()
    for token in selector.split(","):
        match = _OSM_DAY_TOKEN.match(token.strip())
        if not match:
            return None
        first, last = match.groups()
        if first in {"PH", "SH"}:
            continue  # holidays are not known here; the rule never applies
        start = _OSM_DAYS.index(first)
        end = _OSM_DAYS.index(last) if last else start
        index = start
        while True:
            days.add(index)
            if index == end:
                break
            index = (index + 1) % 7
    return days


def _osm(text: str, weekday: int) -> list[Window] | None:
    """OpenStreetMap opening_hours (the common subset). Later rules override earlier ones."""
    text = _normalise(text)
    if _ALL_DAY.match(text):
        return [(0, DAY_MINUTES)]
    result: list[Window] | None = None
    matched = False
    for rule in [part.strip() for part in text.split(";") if part.strip()]:
        selector, _, times = rule.partition(" ")
        if _OSM_DAY_TOKEN.match(selector.split(",")[0]):
            days = _osm_days(selector)
            if days is None:
                return None
            times = times.strip()
        else:
            days, times = set(range(7)), rule
        windows = _spans(times)
        if windows is None:
            return None
        if weekday in days:
            result, matched = windows, True
    return result if matched else None


def _free_text(text: str) -> list[Window] | None:
    """A single span with at most "daily" around it, e.g. "9AM-5PM daily"."""
    stripped = _DAILY_WORDS.sub(" ", _normalise(text)).strip(" ,.;:")
    return _spans(stripped)


def windows_on(lines: list[str], day: date) -> list[Window] | None:
    """Open windows on ``day`` in minutes after midnight.

    ``[]`` when the source says the place is closed that day; ``None`` when the
    source gave no hours or the text cannot be read.
    """
    lines = [str(line) for line in lines or [] if str(line).strip()]
    if not lines:
        return None
    weekday = day.weekday()
    named = [_WEEKDAY_LINE.match(_normalise(line)) for line in lines]
    if any(named):
        for match in named:
            if match and _WEEKDAYS.index(match.group(1).lower()) == weekday:
                return _spans(match.group(2))
        return None
    joined = "; ".join(lines)
    parsed = _osm(joined, weekday)
    if parsed is not None:
        return parsed
    if len(lines) == 1:
        return _free_text(lines[0])
    return None


def only_other_weekdays(lines: list[str], day: date) -> bool:
    """True when the lines are one per weekday ("Monday: ...") and none of them is for ``day``'s weekday."""
    lines = [str(line) for line in lines or [] if str(line).strip()]
    named = [_WEEKDAY_LINE.match(_normalise(line)) for line in lines]
    return any(named) and not any(
        match and _WEEKDAYS.index(match.group(1).lower()) == day.weekday() for match in named
    )


def lines_for(lines: list[str], day: date) -> list[str]:
    """The source's own lines that state the hours for ``day``, verbatim."""
    lines = [str(line) for line in lines or [] if str(line).strip()]
    for line in lines:
        match = _WEEKDAY_LINE.match(_normalise(line))
        if match and _WEEKDAYS.index(match.group(1).lower()) == day.weekday():
            return [line]
    return lines[:7]
