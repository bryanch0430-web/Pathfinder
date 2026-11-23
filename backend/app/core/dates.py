"""Calendar arithmetic shared by understanding, planning and validation."""

from __future__ import annotations

from datetime import date, datetime, timedelta


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    for pattern in ("%Y/%m/%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def day_range(start: date, end: date) -> list[date]:
    if end < start:
        start, end = end, start
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def add_days(start: date, days: int) -> date:
    return start + timedelta(days=max(0, days - 1))


def finish_dates(
    *,
    start: str | None,
    end: str | None,
    days: int | None,
    today: date | None = None,
) -> tuple[str | None, str | None, int | None]:
    """Fill whichever of start/end/nights is missing, without inventing a start date."""
    start_date = parse_date(start)
    end_date = parse_date(end)
    count = int(days) if days else None
    if start_date and end_date and end_date < start_date:
        start_date, end_date = end_date, start_date
    if start_date and count and not end_date:
        end_date = add_days(start_date, count)
    if end_date and count and not start_date:
        start_date = end_date - timedelta(days=max(0, count - 1))
    if start_date and end_date and not count:
        count = (end_date - start_date).days + 1
    if count and count < 1:
        count = 1
    return iso(start_date), iso(end_date), count


def minutes_between(start: str, end: str) -> int:
    first = _minutes(start)
    second = _minutes(end)
    if first is None or second is None:
        return 0
    delta = second - first
    if delta < 0:
        delta += 24 * 60
    return delta


def overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    a1, a2 = _minutes(a_start), _minutes(a_end)
    b1, b2 = _minutes(b_start), _minutes(b_end)
    if None in (a1, a2, b1, b2):
        return False
    return a1 < b2 and b1 < a2


def clock_minutes(value: str | None) -> int | None:
    """Minutes since midnight, or None. Unlike ``minutes_between`` this never wraps."""
    return _minutes(value or "")


def shift_time(value: str, minutes: int) -> str:
    base = _minutes(value)
    if base is None:
        return value
    return _clock((base + minutes) % (24 * 60))


def _minutes(value: str) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) < 2 or not parts[0].strip().isdigit() or not parts[1].strip().isdigit():
        return None
    return int(parts[0]) * 60 + int(parts[1])


def _clock(total: int) -> str:
    return f"{(total // 60) % 24:02d}:{total % 60:02d}"


_MONTHS = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12,
}


def relative_window_label(message: str, today: date | None = None) -> str | None:
    """Name the requested period without inventing a day inside it.

    "Next month" is a window, not a start date. The caller asks the traveller to
    pick the day while keeping the length they already gave.
    """
    import re

    now = today or date.today()
    text = " ".join((message or "").lower().split())
    if not text:
        return None
    if re.search(r"\bnext\s+month\b", text):
        year = now.year + (1 if now.month == 12 else 0)
        month = 1 if now.month == 12 else now.month + 1
        return f"{date(year, month, 1).strftime('%B')} {year}"
    if re.search(r"\bnext\s+weekend\b", text):
        return "next weekend"
    if re.search(r"\bthis\s+weekend\b", text):
        return "this weekend"
    if re.search(r"\bnext\s+week\b", text):
        return "next week"
    if re.search(r"\bthis\s+month\b", text):
        return f"{now.strftime('%B')} {now.year}"
    month_match = re.search(r"\b(?:in|during|for)\s+(" + "|".join(_MONTHS) + r")\b", text)
    if month_match:
        month = _MONTHS[month_match.group(1)]
        year = now.year if month > now.month else now.year + 1
        return f"{date(year, month, 1).strftime('%B')} {year}"
    return None
