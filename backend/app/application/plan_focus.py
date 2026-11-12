"""Which day of the plan a message is about.

The edit and answer prompts carry the whole plan in detail unless the traveller selected a stop or named a
day; this module is the one place that works that out. It decides nothing else, and it imports neither the
edit nor the answer path (both import it).
"""

from __future__ import annotations

import re
from datetime import date

from app.domain.models import TripPlan

_ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}  # fmt: skip
_MONTH_NAMES = "|".join(_MONTHS)


def day_number_named(message: str) -> int | None:
    """The day number a message names ("day 2", "2nd day", "second day"), whether or not the trip has it."""
    lower = message.lower()
    match = re.search(r"\bday\s*(\d{1,2})\b|\b(\d{1,2})(?:st|nd|rd|th)\s+day\b", lower)
    if match:
        return int(match.group(1) or match.group(2))
    for word, index in _ORDINALS.items():
        if re.search(rf"\b{word}\s+day\b", lower):
            return index
    return None


def date_named(question: str, plan: TripPlan | None) -> date | None:
    """A calendar date the question names ("12 April", "April 12"), in or out of the trip.

    The year is the one that puts the date closest to the trip.
    """
    if not plan:
        return None
    lower = question.lower()
    match = re.search(
        rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({_MONTH_NAMES})\b", lower
    )
    if match:
        day_number, month = int(match.group(1)), _MONTHS[match.group(2)]
    else:
        match = re.search(rf"\b({_MONTH_NAMES})\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", lower)
        if not match:
            return None
        day_number, month = int(match.group(2)), _MONTHS[match.group(1)]
    anchor = plan.dates.start
    candidates = []
    for year in (anchor.year - 1, anchor.year, anchor.year + 1):
        try:
            candidates.append(date(year, month, day_number))
        except ValueError:
            continue
    if not candidates:
        return None
    return min(candidates, key=lambda value: abs((value - anchor).days))


def focus_day(plan: TripPlan, message: str, scope_item_id: str | None) -> int | None:
    """The day (from 1) the message is about: the selected stop's day, else a day number the trip has,
    else a date that is one of its days. None when it is about no one day: nothing selected and no day
    named, a selected stay, a selection the plan no longer holds, or a day the trip does not have.
    """
    if scope_item_id:
        found = plan.find_item(scope_item_id)
        if found:
            return plan.days.index(found[0]) + 1
    number = day_number_named(message)
    if number is not None and 1 <= number <= len(plan.days):
        return number
    named = date_named(message, plan)
    if named is not None:
        for index, day in enumerate(plan.days, start=1):
            if day.date == named:
                return index
    return None
