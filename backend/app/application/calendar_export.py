"""The plan as an iCalendar (RFC 5545) file: each stop, each stay and the chosen option of each leg of the trip.

Times are written in the zone of the city they happen in, from the plan's own ``Destination.timezone`` (the segment's
destination). A city with no zone, or a zone name the system does not know, gets floating local time (no TZID), which
a calendar shows at that wall-clock time wherever the reader is; the same holds for the traveller's origin, which the
plan holds no zone for, so a flight's departure from home is floating and its arrival is in the city's zone.

Only names, addresses, coordinates and times are written: no links, prices or notes. Every event's UID is the plan
item's id and the trip id, so importing a later version of the same trip updates the events instead of adding copies.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.models import (
    DayPlan,
    PlanItem,
    TicketOption,
    TransportLeg,
    TripPlan,
    selected_ticket,
)

PRODID = "-//PathFinder//Trip calendar//EN"
ESTIMATE_NOTE = "Estimated travel time: no timetable was looked up."
_STEP = timedelta(minutes=15)


def plan_calendar(plan: TripPlan) -> str:
    """The plan as the text of an .ics file (CRLF lines, folded at 75 octets)."""
    stamp = _utc_text(plan.updated_at)
    zones: dict[str, ZoneInfo] = {}
    events: list[list[str]] = []
    for day in plan.days:
        zone = _day_zone(plan, day)
        for item in sorted(day.items, key=lambda entry: entry.start):
            events.append(_stop_event(plan, day, item, zone, stamp))
            _remember(zones, zone)
    for stay in sorted(plan.hotels, key=lambda entry: entry.check_in):
        lines = [
            f"UID:{_uid(plan, stay.id)}",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{_date_text(stay.check_in)}",
            f"DTEND;VALUE=DATE:{_date_text(max(stay.check_out, stay.check_in + timedelta(days=1)))}",
            f"SUMMARY:{_escape('Stay: ' + stay.name)}",
            "TRANSP:TRANSPARENT",
        ]
        if stay.area:
            lines.append(f"LOCATION:{_escape(stay.area)}")
        events.append(lines)
    for leg in plan.route_legs():
        ticket = selected_ticket(plan.tickets, leg.id)
        if ticket is None:
            continue
        leaves, reaches = (
            _place_zone(plan, leg.from_place),
            _place_zone(plan, leg.to_place),
        )
        event = _leg_event(plan, leg, ticket, leaves, reaches, stamp)
        if event is not None:
            events.append(event)
            _remember(zones, leaves)
            _remember(zones, reaches)
        if ticket.return_depart_at is not None and not ticket.estimated:
            # A round-trip fare carries the way back on the same option: it leaves the city and lands at the origin.
            events.append(
                _timed_event(
                    _uid(plan, ticket.id + "-return"),
                    stamp,
                    f"{ticket.name} (return)",
                    ticket.return_depart_at,
                    ticket.return_arrive_at,
                    reaches,
                    leaves,
                    ticket.return_duration_minutes,
                )
            )
            _remember(zones, reaches)
            _remember(zones, leaves)
    names = (
        " - ".join(segment.destination.name for segment in plan.segments)
        or plan.destination.name
    )
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    lines.append(f"X-WR-CALNAME:{_escape(names)}")
    for key in sorted(zones):
        lines += timezone_block(key, plan.dates.start, plan.dates.end)
    for event in events:
        lines += ["BEGIN:VEVENT", *event, "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "".join(_fold(line) + "\r\n" for line in lines)


def calendar_file_name(plan: TripPlan) -> str:
    slug = "-".join(
        "".join(
            char if char.isascii() and char.isalnum() else "-"
            for char in segment.destination.name.lower()
        )
        for segment in plan.segments
    )
    slug = "-".join(part for part in slug.split("-") if part) or "trip"
    return f"{slug}-{plan.dates.start.isoformat()}.ics"


def timezone_block(key: str, start: date, end: date) -> list[str]:
    """A VTIMEZONE for ``key`` that is right from the day before ``start`` to two days after ``end``: the offset in
    force then, and every change of offset (daylight saving) inside that window, read from the system's zone data."""
    zone = ZoneInfo(key)
    moment = datetime.combine(start - timedelta(days=1), time(), tzinfo=UTC)
    stop = datetime.combine(end + timedelta(days=2), time(), tzinfo=UTC)
    local = moment.astimezone(zone)
    offset = local.utcoffset() or timedelta(0)
    lines = ["BEGIN:VTIMEZONE", f"TZID:{key}"]
    lines += _zone_part(local, datetime(1970, 1, 1), offset, offset)
    while moment < stop:
        moment += _STEP
        local = moment.astimezone(zone)
        now = local.utcoffset() or timedelta(0)
        if now != offset:
            # The onset is written in the wall time that was in force just before the change.
            onset = (moment + offset).replace(tzinfo=None)
            lines += _zone_part(local, onset, offset, now)
            offset = now
    lines.append("END:VTIMEZONE")
    return lines


def _zone_part(
    local: datetime, onset: datetime, before: timedelta, after: timedelta
) -> list[str]:
    kind = "DAYLIGHT" if local.dst() else "STANDARD"
    lines = [
        f"BEGIN:{kind}",
        f"DTSTART:{_datetime_text(onset)}",
        f"TZOFFSETFROM:{_offset_text(before)}",
        f"TZOFFSETTO:{_offset_text(after)}",
    ]
    if local.tzname():
        lines.append(f"TZNAME:{_escape(local.tzname() or '')}")
    lines.append(f"END:{kind}")
    return lines


def _stop_event(
    plan: TripPlan, day: DayPlan, item: PlanItem, zone: ZoneInfo | None, stamp: str
) -> list[str]:
    start = datetime.combine(day.date, time.fromisoformat(item.start))
    end = datetime.combine(day.date, time.fromisoformat(item.end))
    lines = _timed_event(
        _uid(plan, item.id), stamp, item.name, start, end, zone, zone, None
    )
    if item.address:
        lines.append(f"LOCATION:{_escape(item.address)}")
    if item.geo is not None:
        lines.append(f"GEO:{item.geo.lat:.6f};{item.geo.lng:.6f}")
    return lines


def _leg_event(
    plan: TripPlan,
    leg: TransportLeg,
    ticket: TicketOption,
    leaves: ZoneInfo | None,
    reaches: ZoneInfo | None,
    stamp: str,
) -> list[str] | None:
    uid = _uid(plan, ticket.id)
    if ticket.depart_at is not None and not ticket.estimated:
        return _timed_event(
            uid,
            stamp,
            ticket.name,
            ticket.depart_at,
            ticket.arrive_at,
            leaves,
            reaches,
            ticket.duration_minutes,
        )
    # An estimate's clock times are a placeholder, not a timetable: the leg is a day event on its date.
    day = leg.depart_date or (ticket.depart_at.date() if ticket.depart_at else None)
    if day is None:
        return None
    lines = [
        f"UID:{uid}",
        f"DTSTAMP:{stamp}",
        f"DTSTART;VALUE=DATE:{_date_text(day)}",
        f"DTEND;VALUE=DATE:{_date_text(day + timedelta(days=1))}",
        f"SUMMARY:{_escape(ticket.name)}",
        "TRANSP:TRANSPARENT",
    ]
    if ticket.estimated:
        lines.append(f"DESCRIPTION:{_escape(ESTIMATE_NOTE)}")
    return lines


def _timed_event(
    uid: str,
    stamp: str,
    summary: str,
    start: datetime,
    end: datetime | None,
    start_zone: ZoneInfo | None,
    end_zone: ZoneInfo | None,
    minutes: int | None,
) -> list[str]:
    lines = [f"UID:{uid}", f"DTSTAMP:{stamp}", _when("DTSTART", start, start_zone)]
    if end is not None:
        lines.append(_when("DTEND", end, end_zone))
    elif minutes:
        lines.append(f"DURATION:PT{minutes}M")
    lines.append(f"SUMMARY:{_escape(summary)}")
    return lines


def _when(name: str, moment: datetime, zone: ZoneInfo | None) -> str:
    if moment.tzinfo is not None:
        if zone is None:
            return f"{name}:{_utc_text(moment)}"
        moment = moment.astimezone(zone).replace(tzinfo=None)
    if zone is None:
        return f"{name}:{_datetime_text(moment)}"
    return f"{name};TZID={zone.key}:{_datetime_text(moment)}"


def _day_zone(plan: TripPlan, day: DayPlan) -> ZoneInfo | None:
    segment = next(
        (entry for entry in plan.segments if entry.id == day.segment_id), None
    )
    if segment is None:
        segment = plan.segment_of(day.date)
    return _zone(segment.destination.timezone)


def _place_zone(plan: TripPlan, place: str) -> ZoneInfo | None:
    wanted = place.strip().casefold()
    for segment in plan.segments:
        if wanted and segment.destination.name.strip().casefold() == wanted:
            return _zone(segment.destination.timezone)
    return None


def _zone(key: str) -> ZoneInfo | None:
    if not key:
        return None
    try:
        return ZoneInfo(key)
    except (ZoneInfoNotFoundError, ValueError):
        # An unknown zone name: floating local time rather than a wrong zone.
        return None


def _remember(zones: dict[str, ZoneInfo], zone: ZoneInfo | None) -> None:
    if zone is not None:
        zones[zone.key] = zone


def _uid(plan: TripPlan, item_id: str) -> str:
    return f"{item_id}.{plan.trip_id}@pathfinder"


def _escape(text: str) -> str:
    """TEXT value escaping (RFC 5545 3.3.11)."""
    text = text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return text.replace("\r\n", "\\n").replace("\r", "\\n").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Split a content line into lines of at most 75 octets, never inside a UTF-8 character (RFC 5545 3.1)."""
    parts: list[str] = []
    current, size, limit = "", 0, 75
    for char in line:
        width = len(char.encode("utf-8"))
        if size + width > limit:
            parts.append(current)
            current, size, limit = "", 0, 74
        current += char
        size += width
    parts.append(current)
    return "\r\n ".join(parts)


def _date_text(day: date) -> str:
    return day.strftime("%Y%m%d")


def _datetime_text(moment: datetime) -> str:
    return moment.strftime("%Y%m%dT%H%M%S")


def _utc_text(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _offset_text(offset: timedelta) -> str:
    seconds = int(offset.total_seconds())
    sign = "-" if seconds < 0 else "+"
    hours, rest = divmod(abs(seconds), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{sign}{hours:02d}{minutes:02d}" + (f"{seconds:02d}" if seconds else "")
