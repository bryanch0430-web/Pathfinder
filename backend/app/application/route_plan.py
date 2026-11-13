"""The route step of a multi-city trip: nights for each city, the segments they make, and the order of the cities.

Pure functions: nothing here calls a source or a model.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.application.budget_check import PrePlanQuestion
from app.domain.models import Destination, GeoPoint, TripContext, TripSegment, TripStop
from app.infrastructure.http import haversine_km


class RouteQuestion(Exception):
    """The nights and the dates cannot both be met; ``.question`` is what to ask."""

    def __init__(self, question: str) -> None:
        super().__init__(question)
        self.question = question


FEWER_NIGHTS_THAN_CITIES = "The trip has fewer nights than cities. Which cities should I drop, or should the trip be longer?"


def _nights_disagree(listed: int, total: int) -> RouteQuestion:
    return RouteQuestion(
        f"The nights you gave add up to {listed} but the dates cover {total}. Which should I follow?"
    )


def allocate_nights(stops: list[TripStop], total_nights: int) -> tuple[list[int], str]:
    """Nights per stop, and a note for the reply ("" when nothing was adjusted).

    Every stop needs at least one night. Nights the traveller stated are kept; the unstated stops share what is left
    evenly, the remainder going to the earlier ones. When every stop is stated and they add up to less than the dates
    cover, the extra goes to the first stop and the note says so. A conflict is a ``RouteQuestion``, never a guess.
    """
    if total_nights < len(stops):
        raise RouteQuestion(FEWER_NIGHTS_THAN_CITIES)
    stated = sum(stop.nights or 0 for stop in stops)
    unstated = [index for index, stop in enumerate(stops) if stop.nights is None]
    if stated > total_nights:
        raise _nights_disagree(stated, total_nights)
    nights = [stop.nights or 0 for stop in stops]
    if unstated:
        left = total_nights - stated
        if left < len(unstated):
            raise _nights_disagree(stated + len(unstated), total_nights)
        share, remainder = divmod(left, len(unstated))
        for position, index in enumerate(unstated):
            nights[index] = share + (1 if position < remainder else 0)
        return nights, ""
    extra = total_nights - stated
    if extra > 0:
        nights[0] += extra
        plural = "nights" if extra != 1 else "night"
        pronoun = "them" if extra != 1 else "it"
        return nights, (
            f"The dates cover {extra} more {plural} than you listed; I added {pronoun} to {stops[0].city}."
        )
    return nights, ""


def route_question(context: TripContext) -> PrePlanQuestion | None:
    """The question to ask before planning when the nights of the cities and the trip's dates cannot both be met."""
    if len(context.route) < 2:
        return None
    try:
        dates = context.date_range()
    except ValueError:  # the form's own checks report an end before the start or a trip that is too long
        return None
    if dates is None:
        return None
    try:
        allocate_nights(context.route, dates.night_count)
    except RouteQuestion as asked:
        return PrePlanQuestion(question=asked.question, slots=["dates"], reason_code="route_nights")
    return None


def build_segments(
    places: list[Destination], nights: list[int], start: date
) -> list[TripSegment]:
    """Consecutive segments: each starts the day the one before ends. Ids are ``seg_1``, ``seg_2`` and so on."""
    segments: list[TripSegment] = []
    arrival = start
    for number, (place, stay) in enumerate(zip(places, nights), 1):
        departure = arrival + timedelta(days=stay)
        segments.append(
            TripSegment(
                id=f"seg_{number}",
                destination=place,
                dates={"start": arrival, "end": departure},
            )
        )
        arrival = departure
    return segments


def nearest_order(origin: GeoPoint | None, places: list[Destination]) -> list[int]:
    """Indexes of ``places`` in nearest-neighbour order from ``origin`` (from ``places[0]`` when there is none).

    A place with no coordinates cannot be measured: it keeps its place in the original order among those left, after
    the ones that can.
    """
    remaining = list(range(len(places)))
    order: list[int] = []
    here = origin
    if here is None and remaining:
        first = remaining.pop(0)
        order.append(first)
        here = places[first].geo
    while remaining:
        measured = [i for i in remaining if places[i].geo is not None]
        if here is None or not measured:
            order.extend(remaining)
            break
        nearest = min(
            measured,
            key=lambda i: haversine_km(
                here.lat, here.lng, places[i].geo.lat, places[i].geo.lng
            ),
        )
        remaining.remove(nearest)
        order.append(nearest)
        here = places[nearest].geo
    return order
