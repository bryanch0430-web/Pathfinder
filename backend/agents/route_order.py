"""Daily route ordering, isolated behind `RouteOrderer` so it can be replaced.

Proposal: the planner "optimises the daily route to prevent backtracking". The default is a
greedy nearest-neighbour tour from the day's start point (the hotel). It is O(n^2), needs no
external service, and never produces a tour that returns to an already-visited area while a
closer unvisited stop exists. A better solver (2-opt, a maps routing API) can implement the
same protocol without touching the planner.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from backend.agents.geo import haversine_km
from backend.schemas.common import GeoPoint
from backend.schemas.trip_plan import ItineraryItem

Locate = Callable[[str], GeoPoint | None]


class RouteOrderer(Protocol):
    def order(
        self, start: GeoPoint | None, items: Sequence[ItineraryItem], locate: Locate
    ) -> list[ItineraryItem]: ...


class NearestNeighbourOrderer:
    def order(
        self, start: GeoPoint | None, items: Sequence[ItineraryItem], locate: Locate
    ) -> list[ItineraryItem]:
        located = [(item, locate(item.place_id)) for item in items]
        remaining = [(item, point) for item, point in located if point is not None]
        unlocated = [item for item, point in located if point is None]
        ordered: list[ItineraryItem] = []
        current = start
        if current is None and remaining:
            first_item, current = remaining.pop(0)
            ordered.append(first_item)
        while remaining and current is not None:
            here = current
            best = min(
                range(len(remaining)),
                key=lambda i: (haversine_km(here, remaining[i][1]), remaining[i][0].item_id),
            )
            item, point = remaining.pop(best)
            ordered.append(item)
            current = point
        return ordered + unlocated


def route_length_km(start: GeoPoint | None, items: Sequence[ItineraryItem], locate: Locate) -> float:
    """Total straight-line length of visiting `items` in order from `start`."""
    total = 0.0
    previous = start
    for item in items:
        point = locate(item.place_id)
        if point is None:
            continue
        if previous is not None:
            total += haversine_km(previous, point)
        previous = point
    return total
