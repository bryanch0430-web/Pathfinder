"""Route continuity: the days and the city segments must connect without gaps.

This is the deterministic part of the multi-city requirement. It checks the
shape of the plan the planner produced; it does not pretend to be the multi-city
composer.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.core.dates import parse_date
from app.domain.models import TripContext, TripPlan
from app.plan.builder import Problem


def _span(segment: object) -> tuple[date | None, date | None, str]:
    destination = getattr(segment, "destination", {}) or {}
    city = destination.get("name", "") if isinstance(destination, dict) else ""
    dates = getattr(segment, "dates", {}) or {}
    return parse_date(dates.get("start")), parse_date(dates.get("end")), str(city)


def route_continuity_problems(plan: TripPlan, context: TripContext) -> list[Problem]:
    problems: list[Problem] = []
    if not plan.days:
        return [Problem(code="route_empty", message="The route has no days.")]

    # 1. Days must be unique, ordered and consecutive from the trip start.
    start = parse_date(context.start_date)
    day_dates: list[date] = []
    for index, day in enumerate(plan.days, start=1):
        value = parse_date(day.date)
        if value is None:
            problems.append(Problem(code="route_bad_date", message=f"Day {index} has an invalid date."))
            continue
        day_dates.append(value)
    if start is not None and day_dates:
        expected = start
        for value in day_dates:
            if value != expected:
                problems.append(
                    Problem(
                        code="route_date_gap",
                        message=(
                            f"The route jumps from {expected.isoformat()} to {value.isoformat()}; "
                            "days must stay consecutive."
                        ),
                    )
                )
                break
            expected += timedelta(days=1)

    # 2. A multi-city route needs ordered, non-overlapping segments that match
    #    the requested city order. A single-city plan has one segment.
    if len(context.stops) > 1:
        if len(plan.segments) != len(context.stops):
            problems.append(
                Problem(
                    code="route_segments_missing",
                    message=(
                        f"The route asked for {len(context.stops)} city segments but the plan "
                        f"has {len(plan.segments)}."
                    ),
                )
            )
        requested = [stop.city.strip() for stop in context.stops if stop.city.strip()]
        actual: list[str] = []
        previous_end: date | None = None
        for index, segment in enumerate(plan.segments, start=1):
            seg_start, seg_end, city = _span(segment)
            actual.append(city)
            if seg_start is None or seg_end is None:
                problems.append(Problem(code="route_segment_dates", message=f"Segment {index} has no dates."))
                continue
            if seg_start > seg_end:
                problems.append(Problem(code="route_segment_order", message=f"Segment {index} ends before it starts."))
            if previous_end is not None and seg_start <= previous_end:
                problems.append(
                    Problem(
                        code="route_segment_overlap",
                        message=f"Segment {index} ({city}) overlaps the previous city segment.",
                    )
                )
            previous_end = seg_end
        if requested and actual[: len(requested)] != requested:
            problems.append(
                Problem(
                    code="route_city_order",
                    message=(
                        "The city order is not continuous: the request was "
                        + " → ".join(requested)
                        + " but the plan is "
                        + " → ".join(actual[: len(requested)])
                        + "."
                    ),
                )
            )
        for index, day in enumerate(plan.days, start=1):
            if context.stops and not day.segment_id:
                problems.append(
                    Problem(
                        code="route_day_segment",
                        message=f"Day {index} is not assigned to a city segment.",
                        day=index,
                    )
                )
    return problems[:10]
