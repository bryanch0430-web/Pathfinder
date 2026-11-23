"""Flight boundaries: a day may not schedule stops before arrival or after departure.

The model proposes the order; this module is the deterministic boundary check.
It is deliberately small and v5-specific: single-city trips with selected flight
tickets. Multi-city segments get their own windows when the multi-city planner
adds them.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.domain.models import TripContext, TripPlan
from app.plan.builder import Problem

# Immigration, baggage and the transfer into town. It is a planning buffer, not a
# promise about the airline's actual boarding time.
ARRIVAL_BUFFER_MINUTES = 90
DEPARTURE_BUFFER_MINUTES = 120


def _moment(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(text[:10]), time(0, 0))
        except ValueError:
            return None
    return parsed


def _clock_minutes(value: str | None) -> int | None:
    parts = (value or "").strip().split(":")
    if len(parts) < 2 or not parts[0].strip().isdigit() or not parts[1].strip().isdigit():
        return None
    return int(parts[0]) * 60 + int(parts[1])


def _flight_tickets(plan: TripPlan):
    tickets = [ticket for ticket in plan.tickets if ticket.mode == "flight"]
    selected = [ticket for ticket in tickets if ticket.selected]
    return selected[:2] if selected else tickets[:1]


def flight_windows(plan: TripPlan) -> dict[date, tuple[int | None, int | None, list[str]]]:
    """Return the allowed clock window for each travel day.

    A tuple is ``(earliest_start_minutes, latest_end_minutes, notes)``. ``None``
    means that side is not constrained by a flight.
    """
    windows: dict[date, tuple[int | None, int | None, list[str]]] = {}
    for ticket in _flight_tickets(plan):
        legs = (
            (ticket.depart_at, ticket.arrive_at, "outbound"),
            (ticket.return_depart_at, ticket.return_arrive_at, "return"),
        )
        for depart_raw, arrive_raw, label in legs:
            depart = _moment(depart_raw)
            arrive = _moment(arrive_raw)
            if arrive is not None:
                day = arrive.date()
                start = _clock_minutes(arrive.strftime("%H:%M"))
                if start is not None:
                    start += ARRIVAL_BUFFER_MINUTES
                old_start, old_end, notes = windows.get(day, (None, None, []))
                if start is not None and (old_start is None or start > old_start):
                    old_start = start
                windows[day] = (old_start, old_end, [*notes, f"{label} arrival"])
            if depart is not None:
                day = depart.date()
                end = _clock_minutes(depart.strftime("%H:%M"))
                if end is not None:
                    end -= DEPARTURE_BUFFER_MINUTES
                old_start, old_end, notes = windows.get(day, (None, None, []))
                if end is not None and (old_end is None or end < old_end):
                    old_end = end
                windows[day] = (old_start, old_end, [*notes, f"{label} departure"])
    return windows


def flight_boundary_problems(plan: TripPlan, context: TripContext) -> list[Problem]:
    if len(context.stops) > 1:
        # A multi-city plan carries intercity legs; its own segment checks own
        # those boundaries. Do not apply the first flight to every city.
        return []
    windows = flight_windows(plan)
    problems: list[Problem] = []
    for index, day in enumerate(plan.days, start=1):
        try:
            day_date = date.fromisoformat(day.date)
        except ValueError:
            continue
        window = windows.get(day_date)
        if window is None:
            continue
        earliest, latest, notes = window
        label = ", ".join(notes)
        for item in day.items:
            start = _clock_minutes(item.start)
            end = _clock_minutes(item.end) or start
            if earliest is not None and start is not None and start < earliest:
                problems.append(
                    Problem(
                        code="before_flight_arrival",
                        message=(
                            f"Day {index}: {item.name} starts at {item.start}, before the "
                            f"flight window ({label}). Leave at least "
                            f"{ARRIVAL_BUFFER_MINUTES} minutes after arrival."
                        ),
                        day=index,
                        item_id=item.id,
                    )
                )
            if latest is not None and end is not None and end > latest:
                problems.append(
                    Problem(
                        code="after_flight_departure",
                        message=(
                            f"Day {index}: {item.name} ends at {item.end}, after the "
                            f"flight window ({label}). Leave at least "
                            f"{DEPARTURE_BUFFER_MINUTES} minutes before departure."
                        ),
                        day=index,
                        item_id=item.id,
                    )
                )
    return problems[:8]
