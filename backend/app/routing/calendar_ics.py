"""The trip document as an iCalendar file: stops, stays, and the route's legs.

Times follow the morning / afternoon / evening block. Stays are all-day. A leg has
no timetable, so it is an all-day event with that said in the description. UIDs
are stable for a trip, so importing a later version updates the events.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from app.schemas.trip_document import TripDocument

PRODID = "-//Pathfinder//Trip calendar//EN"
BLOCKS = {"morning": (9 * 60, 12 * 60), "afternoon": (13 * 60, 17 * 60), "evening": (18 * 60, 21 * 60)}


def document_to_ics(document: TripDocument, *, now: datetime | None = None) -> str | None:
    if document.start_date is None:
        return None
    stamp = _stamp(now or datetime.now(timezone.utc))
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_text(document.destination or 'Trip')}",
    ]
    for day in document.days:
        if day.date is None:
            continue
        booked = {"morning": 0, "afternoon": 0, "evening": 0}
        counts = {slot: sum(1 for item in day.items if item.time_of_day == slot) for slot in BLOCKS}
        for index, item in enumerate(day.items):
            summary = ("Stay: " if item.category == "stay" else "") + item.name + (" (option)" if item.option else "")
            uid = f"{document.trip_id}-d{day.day}-{index}"
            location = ", ".join(part for part in (item.area, day.base) if part)
            description = "\n".join(part for part in (
                f"Day {day.day} · {day.base}",
                item.tip or "",
                "Estimated: no timetable was looked up." if item.category == "transit" else "",
            ) if part)
            start_m = None if item.category == "stay" else _minutes(item.start)
            if item.category == "stay" or (start_m is None and not item.time_of_day):
                lines.extend(_event(uid, stamp, summary, description, location, start_date=day.date))
                continue
            if start_m is not None:
                end_m = _minutes(item.end)
                length = end_m - start_m if end_m and end_m > start_m else max(30, round((item.duration_hours or 1.5) * 60))
                lines.extend(_event(
                    uid, stamp, summary, description, location,
                    start=_on(day.date, start_m), end=_on(day.date, min(start_m + length, 23 * 60 + 59)),
                ))
                continue
            open_m, close_m = BLOCKS[item.time_of_day]
            share = max(30, (close_m - open_m) // max(1, counts[item.time_of_day]))
            length = max(30, round((item.duration_hours or 0) * 60) or share)
            start = min(open_m + booked[item.time_of_day], 23 * 60 - 30)
            booked[item.time_of_day] += length
            lines.extend(_event(
                uid, stamp, summary, description, location,
                start=_on(day.date, start), end=_on(day.date, min(start + length, 23 * 60 + 59)),
            ))
    for index, leg in enumerate(document.legs):
        summary = f"{leg.source} → {leg.target}"
        description = "\n".join(part for part in (leg.mode, leg.duration, leg.distance, leg.cost, "Estimated: no timetable was looked up.") if part)
        lines.extend(_event(f"{document.trip_id}-leg-{index}", stamp, summary, description, leg.target, start_date=document.start_date))
    for holiday in document.enrichment.holidays if document.enrichment else []:
        lines.extend(_event(
            f"{document.trip_id}-holiday-{holiday.date}-{_slug(holiday.name)}", stamp,
            f"Public holiday: {holiday.name}", "Some places may close. Source: Nager.Date", "",
            start_date=holiday.date,
        ))
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def _event(uid, stamp, summary, description, location, *, start=None, end=None, start_date: date | None = None) -> list[str]:
    when = [f"DTSTART;VALUE=DATE:{start_date.strftime('%Y%m%d')}", f"DTEND;VALUE=DATE:{(start_date + timedelta(days=1)).strftime('%Y%m%d')}"] \
        if start_date is not None else [f"DTSTART:{start}", f"DTEND:{end}"]
    rows = ["BEGIN:VEVENT", f"UID:{uid}@pathfinder", f"DTSTAMP:{stamp}", *when, f"SUMMARY:{_text(summary)}"]
    if description:
        rows.append(f"DESCRIPTION:{_text(description)}")
    if location:
        rows.append(f"LOCATION:{_text(location)}")
    rows.append("END:VEVENT")
    return rows


def _minutes(value: str | None) -> int | None:
    if not value or len(value) != 5 or value[2] != ":":
        return None
    hour, minute = value[:2], value[3:]
    if not (hour.isdigit() and minute.isdigit()):
        return None
    h, m = int(hour), int(minute)
    if h > 23 or m > 59:
        return None
    return h * 60 + m


def _on(day: date, minutes: int) -> str:
    return f"{day.strftime('%Y%m%d')}T{minutes // 60:02d}{minutes % 60:02d}00"


def _stamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _text(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n")


def _slug(value: str) -> str:
    return re.sub(r"\s+", "-", value.strip())[:40]


def _fold(line: str) -> str:
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    parts: list[str] = []
    current = ""
    size = 0
    limit = 75
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > limit:
            parts.append(current)
            current = ""
            size = 0
            limit = 74
        current += char
        size += width
    parts.append(current)
    return "\r\n ".join(parts)
