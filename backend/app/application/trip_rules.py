"""Traveller rules the planner must honour with or without a model.

Three kinds of rule are read straight from the traveller's own constraints and
tags: a day kept free ("19/11 cannot have any plan"), an arrival or departure
time ("18:35 到仁川機場", "19:35 上機") and a named place to include. They are
deterministic so an offline planner obeys them exactly like a model-backed one.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable

from app.application.understanding import PREFERENCE_PATTERNS
from app.domain.models import PlaceCandidate, TripContext

#: Airport to city plus check-in after landing, in minutes.
ARRIVAL_TRANSFER_MINUTES = 120
#: City to airport plus check-in before an international flight, in minutes.
DEPARTURE_BUFFER_MINUTES = 210
MAX_WISHES = 4

_MONTHS = {name.lower(): number for number, name in enumerate(calendar.month_abbr) if name}
_MONTH = "|".join(_MONTHS)

_FREE_DAY = re.compile(
    r"\b(?:no\s+(?:plans?|activities|stops|itinerary|schedule)"
    r"|can(?:not|'?t)\s+have\s+any\s*(?:plans?|activities|stops)"
    r"|nothing\s+planned|free\s+day|rest\s+day|day\s+off"
    r"|keep\s+(?:it\s+|the\s+day\s+)?free|leave\s+(?:it\s+|the\s+day\s+)?(?:empty|free|open))\b"
    r"|(?:唔好|不要|不用|無|冇|没有|沒有)\s*(?:安排)?\s*(?:任何)?\s*(?:行程|活動|活动|plan)"
    r"|休息日|自由活動|自由活动|留空",
    re.IGNORECASE,
)
_TIME = re.compile(
    r"(?<![\d/.])(\d{1,2})(?:[:：](\d{2}))?\s*(am|pm)\b|(?<![\d/.])(\d{1,2})[:：](\d{2})(?!\d)",
    re.IGNORECASE,
)
_ARRIVE = re.compile(
    r"arriv|\bland(?:s|ing|ed)?\b|touch(?:es)?\s*down|到達|抵達|落機|到埗|到\S{0,4}(?:機場|机场|airport)",
    re.IGNORECASE,
)
_LEAVE_FOR_AIRPORT = re.compile(
    r"\b(?:leave|head|go|set off)\s+(?:for|to)\s+the\s+airport\b|出機場|去機場|返機場|往機場|去机场",
    re.IGNORECASE,
)
_DEPART = re.compile(
    r"\bdepart|\bfly\s+(?:out|back|home)|\bflight|\btake\s*off|\bboard|上機|登機|起飛|回程|返程|上机|起飞",
    re.IGNORECASE,
)


@dataclass
class DayWindow:
    """The part of a travel day that is free for stops, in minutes after midnight."""

    start: int | None = None
    end: int | None = None
    texts: list[str] = field(default_factory=list)


def mentioned_dates(text: str, dates: Iterable[date]) -> set[date]:
    """Trip dates a sentence names: 19/11, 2026-11-19, Nov 19, 19 Nov, 11月19日, day 2, 第2日."""
    trip = list(dates)
    lower = text.lower()
    found: set[date] = set()

    def add(month: int, day: int, year: int | None = None) -> None:
        for candidate in trip:
            if candidate.month == month and candidate.day == day and (year is None or candidate.year == year):
                found.add(candidate)

    for year, month, day in re.findall(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", lower):
        add(int(month), int(day), int(year))
    for first, second, year in re.findall(r"(?<![\d:])(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?(?![\d:])", lower):
        full_year = None
        if year:
            full_year = int(year) + 2000 if len(year) == 2 else int(year)
        # Day/month and month/day both read; only a real trip date survives.
        add(int(second), int(first), full_year)
        add(int(first), int(second), full_year)
    for day, month in re.findall(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH})[a-z]*\b", lower):
        add(_MONTHS[month], int(day))
    for month, day in re.findall(rf"\b({_MONTH})[a-z]*\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", lower):
        add(_MONTHS[month], int(day))
    for month, day in re.findall(r"(\d{1,2})\s*月\s*(\d{1,2})", text):
        add(int(month), int(day))
    for number in re.findall(r"\bday\s*(\d{1,2})\b", lower) + re.findall(r"第\s*(\d{1,2})\s*[日天]", text):
        index = int(number) - 1
        if 0 <= index < len(trip):
            found.add(trip[index])
    return found


def blocked_dates(texts: Iterable[str], dates: Iterable[date]) -> dict[date, str]:
    """Trip dates the traveller wants kept free, each with the sentence that said so."""
    trip = list(dates)
    blocked: dict[date, str] = {}
    for text in texts:
        if not _FREE_DAY.search(text):
            continue
        for day in mentioned_dates(text, trip):
            blocked.setdefault(day, text)
    return blocked


def _first_time(text: str) -> int | None:
    match = _TIME.search(text)
    if not match:
        return None
    if match.group(3):
        hours = int(match.group(1)) % 12 + (12 if match.group(3).lower() == "pm" else 0)
        minutes = int(match.group(2) or 0)
    else:
        hours, minutes = int(match.group(4)), int(match.group(5))
    if hours > 23 or minutes > 59:
        return None
    return hours * 60 + minutes


def travel_windows(texts: Iterable[str], dates: Iterable[date]) -> dict[date, DayWindow]:
    """Arrival and departure times as the free part of the first and last day.

    A landing time opens the day ARRIVAL_TRANSFER_MINUTES later; a flight time
    closes it DEPARTURE_BUFFER_MINUTES earlier; a time to leave for the airport
    closes it at that time. A sentence that names a trip date applies to that
    date, otherwise an arrival is the first day and a departure the last.
    """
    trip = list(dates)
    windows: dict[date, DayWindow] = {}
    if not trip:
        return windows
    for text in texts:
        minute = _first_time(text)
        if minute is None:
            continue
        named = sorted(mentioned_dates(text, trip))
        if _ARRIVE.search(text):
            day = named[0] if named else trip[0]
            window = windows.setdefault(day, DayWindow())
            start = minute + ARRIVAL_TRANSFER_MINUTES
            window.start = start if window.start is None else max(window.start, start)
        elif _LEAVE_FOR_AIRPORT.search(text) or _DEPART.search(text):
            end = minute if _LEAVE_FOR_AIRPORT.search(text) else minute - DEPARTURE_BUFFER_MINUTES
            day = named[-1] if named else trip[-1]
            window = windows.setdefault(day, DayWindow())
            window.end = end if window.end is None else min(window.end, end)
        else:
            continue
        window.texts.append(text)
    return windows


def _clock(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def flight_windows(flights: Iterable[dict], dates: Iterable[date]) -> dict[date, DayWindow]:
    """The chosen flight opens the arrival day and closes the departure day.

    Only the first option is used. A time that is not on a trip date is ignored.
    """
    trip = list(dates)
    rows = list(flights)
    windows: dict[date, DayWindow] = {}
    if not trip or not rows:
        return windows
    row = rows[0]
    arrive = _clock(row.get("arrive_at"))
    if arrive is not None and arrive.date() in trip:
        start = arrive.hour * 60 + arrive.minute + ARRIVAL_TRANSFER_MINUTES
        window = windows.setdefault(arrive.date(), DayWindow())
        window.start = start if window.start is None else max(window.start, start)
        window.texts.append("flight arrival")
    leave = _clock(row.get("return_depart_at"))
    if leave is not None and leave.date() in trip:
        end = leave.hour * 60 + leave.minute - DEPARTURE_BUFFER_MINUTES
        window = windows.setdefault(leave.date(), DayWindow())
        window.end = end if window.end is None else min(window.end, end)
        window.texts.append("flight departure")
    return windows


def must_include(text: str) -> str | None:
    lower = text.lower()
    if re.search(r"\b(?:do not|don't|not|never|exclude|without)\s+(?:include|visit)\b", lower):
        # "do not include the closed Peak Tram" is an exclusion, not a must-include.
        return None
    for marker in ("must include", "must visit", "need to include", "want to visit", "include"):
        if marker in lower:
            value = text[lower.index(marker) + len(marker) :].strip(" :.-")
            # "the Ghibli Museum" must match a stop named "Ghibli Museum".
            value = re.sub(r"^(?:the|a|an)\s+", "", value, flags=re.IGNORECASE)
            return value[:60] or None
    return None


def _is_rule_tag(tag: str) -> bool:
    """A tag the rules already read: an interest, a pace, a diet, a time or a free day."""
    lower = tag.strip().lower()
    if lower in PREFERENCE_PATTERNS or lower in {"balanced", "slow", "fast"}:
        return True
    if any(re.fullmatch(pattern, lower) for pattern in PREFERENCE_PATTERNS.values()):
        return True
    return bool(_FREE_DAY.search(tag) or _first_time(tag) is not None)


def place_wishes(context: TripContext) -> list[str]:
    """Places the traveller named: must-see tags, must-include constraints, then free-text tags."""
    wishes: list[str] = []
    for item in context.preferences:
        text = str(item)
        if text.lower().startswith("must-see:"):
            wishes.append(text.split(":", 1)[1].strip())
    for text in context.hard_constraints:
        needle = must_include(text)
        if needle:
            wishes.append(needle)
    for item in context.preferences:
        text = str(item).strip()
        if text.lower().startswith("must-see:") or not 2 <= len(text) <= 60 or _is_rule_tag(text):
            continue
        wishes.append(text)
    unique: list[str] = []
    seen: set[str] = set()
    for wish in wishes:
        key = wish.lower()
        if wish and key not in seen:
            seen.add(key)
            unique.append(wish)
    return unique[:MAX_WISHES]


def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def wish_matches(place: PlaceCandidate, wish: str) -> bool:
    """True when the place's name or one of its aliases is the place the traveller named.

    A keyless search answers a query it cannot read with an unrelated row (a
    shop called "T" for 聖水洞), so the name itself has to agree.
    """
    wanted = _norm(wish)
    if len(wanted) < 2:
        return False
    for name in [place.name, *place.aliases]:
        candidate = _norm(name)
        if len(candidate) < 2:
            continue
        if wanted in candidate:
            return True
        # "和談林" answers "和談林賞楓"; the city name "Seoul" does not answer "Seoul Forest".
        if candidate in wanted and len(candidate) * 2 >= len(wanted):
            return True
    return False
