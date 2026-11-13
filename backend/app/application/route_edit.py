"""Changing the route of a multi-city plan from chat: add, remove or reorder a city, change a city's nights, give a
stay a second hotel for its last nights.

The trip's dates stay as they are, so every change moves nights between cities and the sum never changes. The functions
here are pure: they work out the new route (and say which nights moved); planning what changed is ``multi_edit``.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from app.application.edit_scope import which_visit_question
from app.application.route_plan import RouteQuestion, nearest_order
from app.domain.models import TRIP_STOPS_MAX, TripContext, TripPlan, TripStop

WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
}


#: Added to the edit prompt of a plan of several cities only, so an edit of one city costs what it always did.
MULTI_CITY_RULES = (
    "This plan has several cities; a stay line or a day header names its city. Add `route_change` only when the "
    "traveller asks to add, drop or reorder a city or to change a city's nights: "
    '{"op": "add"|"remove"|"reorder"|"nights", "city": "...", "after": "city or null", "nights": 2, '
    '"order": ["..."]} (`order` empty for the best order); never for a stop inside a city. Add `split_stay` only for '
    '"another hotel for the last nights of a city": {"city": "Tokyo", "last_nights": 2}.\n\n'
)


class RouteChange(BaseModel):
    """What the traveller asked to do with the route. ``after`` is the city an added one follows (None: the end);
    ``order`` is the whole new order of a reorder (empty: the nearest-neighbour order)."""

    op: Literal["add", "remove", "reorder", "nights"]
    city: str | None = None
    after: str | None = None
    nights: int | None = Field(default=None, ge=1, le=60)
    order: list[str] = Field(default_factory=list)


class SplitStay(BaseModel):
    """A second hotel for the last ``last_nights`` nights of a city (None: the city the message settles)."""

    city: str | None = None
    last_nights: int = Field(ge=1, le=60)


def _nights(count: int) -> str:
    return f"{count} night{'s' if count != 1 else ''}"


def _index(plan: TripPlan, route: list[list], city: str | None) -> int:
    wanted = (city or "").strip().casefold()
    hits = [i for i, (name, _) in enumerate(route) if name.casefold() == wanted]
    if not hits:
        raise RouteQuestion(
            f"{city or 'That city'} is not one of the cities of the trip."
        )
    if len(hits) > 1:
        raise RouteQuestion(which_visit_question(plan, city or ""))
    return hits[0]


def _longest(route: list[list], *, besides: int | None = None) -> int:
    candidates = [i for i in range(len(route)) if i != besides]
    return max(candidates, key=lambda i: route[i][1])


def apply_route_change(
    plan: TripPlan, change: RouteChange
) -> tuple[list[TripStop], str]:
    """The new ordered stops with their nights, and a sentence saying which nights moved.

    A change that cannot be made without guessing is a ``RouteQuestion``.
    """
    route = [
        [segment.destination.name, segment.dates.night_count]
        for segment in plan.segments
    ]
    if change.op == "add":
        sentence = _add(plan, route, change)
    elif change.op == "remove":
        sentence = _remove(plan, route, change)
    elif change.op == "nights":
        sentence = _set_nights(plan, route, change)
    else:
        sentence = _reorder(plan, route, change)
    return [TripStop(city=name, nights=count) for name, count in route], sentence


def _add(plan: TripPlan, route: list[list], change: RouteChange) -> str:
    city = (change.city or "").strip()
    if not city:
        raise RouteQuestion("Which city should I add?")
    if len(route) >= TRIP_STOPS_MAX:
        raise RouteQuestion(
            f"A trip holds up to {TRIP_STOPS_MAX} cities. Which one should I drop to make room?"
        )
    donor = _longest(route)
    take = change.nights or max(1, route[donor][1] // 2)
    if route[donor][1] - take < 1:
        raise RouteQuestion(
            f"{route[donor][0]} has only {_nights(route[donor][1])}, so I can't take {_nights(take)} for {city}. "
            "Which city should give nights, or should the trip be longer?"
        )
    position = _index(plan, route, change.after) + 1 if change.after else len(route)
    giver = route[donor]
    giver[1] -= take
    route.insert(position, [city, take])
    return f"Added {city} for {_nights(take)}, taken from {giver[0]}, which now has {giver[1]}."


def _remove(plan: TripPlan, route: list[list], change: RouteChange) -> str:
    if len(route) == 1:
        raise RouteQuestion(
            "That is the only city of the trip. To go somewhere else, plan a new trip."
        )
    index = _index(plan, route, change.city)
    city, count = route.pop(index)
    receiver = route[index - 1] if index > 0 else route[0]
    receiver[1] += count
    return f"Removed {city}; its {_nights(count)} went to {receiver[0]}."


def _set_nights(plan: TripPlan, route: list[list], change: RouteChange) -> str:
    index = _index(plan, route, change.city)
    if change.nights is None:
        raise RouteQuestion(f"How many nights in {route[index][0]}?")
    if len(route) == 1:
        raise RouteQuestion(
            "The trip has one city, so its nights are the trip's dates. Change the dates instead."
        )
    delta = change.nights - route[index][1]
    if delta == 0:
        return f"{route[index][0]} already has {_nights(change.nights)}."
    donor = _longest(route, besides=index)
    if route[donor][1] - delta < 1:
        raise RouteQuestion(
            f"{route[donor][0]} has only {_nights(route[donor][1])}, so I can't give {route[index][0]} "
            f"{_nights(change.nights)}. Which city should give nights, or should the trip be longer?"
        )
    route[index][1] = change.nights
    route[donor][1] -= delta
    verb = "taken from" if delta > 0 else "given to"
    return (
        f"{route[index][0]} now has {_nights(change.nights)}, {abs(delta)} {verb} {route[donor][0]}, "
        f"which now has {route[donor][1]}."
    )


def _reorder(plan: TripPlan, route: list[list], change: RouteChange) -> str:
    if change.order:
        remaining = list(route)
        ordered: list[list] = []
        for name in change.order:
            hit = next(
                (
                    row
                    for row in remaining
                    if row[0].casefold() == name.strip().casefold()
                ),
                None,
            )
            if hit is None:
                raise RouteQuestion(f"{name} is not one of the cities of the trip.")
            remaining.remove(hit)
            ordered.append(hit)
        if remaining:
            raise RouteQuestion(
                "Which order should all of the cities be in? "
                + ", ".join(row[0] for row in route)
            )
    else:
        places = [segment.destination for segment in plan.segments]
        ordered = [route[i] for i in nearest_order(None, places)]
    route[:] = ordered
    return (
        "Reordered the cities: "
        + " → ".join(row[0] for row in route)
        + ". Each city keeps its nights."
    )


# -- rules for a message, for the offline path ------------------------------------------------------------------------

_CITY_WORD = r"[A-Za-z][A-Za-z'’.\-]*"
_NAME = rf"{_CITY_WORD}(?:\s+{_CITY_WORD}){{0,2}}"


def _known_cities() -> set[str]:
    from app.application.understanding import CITY_HINTS

    return {city.casefold() for city in CITY_HINTS}


def _segment_city(plan: TripPlan, text: str) -> str | None:
    """The name of the plan's city that ``text`` is (any case), else None."""
    wanted = text.strip().casefold()
    return next(
        (
            s.destination.name
            for s in plan.segments
            if s.destination.name.casefold() == wanted
        ),
        None,
    )


def _mentioned_cities(plan: TripPlan, message: str) -> list[str]:
    lower = message.lower()
    seen: list[str] = []
    for segment in plan.segments:
        name = segment.destination.name
        if (
            re.search(rf"(?<!\w){re.escape(name.lower())}(?!\w)", lower)
            and name not in seen
        ):
            seen.append(name)
    return seen


def rule_route_change(message: str, plan: TripPlan) -> RouteChange | None:
    """A route change the words of the message state plainly, or None. It is narrow on purpose: a message it does not
    match changes nothing, and "add a museum" is never a city."""
    text = " ".join(message.split())
    lower = text.lower()
    order_words = re.search(
        r"\b(?:best|nearest|shortest|better|optimal|optimi[sz]e)\b[^.!?]{0,30}\b(?:order|route)\b",
        lower,
    )
    if order_words and len(plan.segments) > 2:
        return RouteChange(op="reorder")
    swap = re.search(
        rf"\bswap\s+(?:the\s+)?({_NAME})\s+and\s+(?:the\s+)?({_NAME})\b",
        text,
        re.IGNORECASE,
    )
    if swap:
        first, second = (
            _segment_city(plan, swap.group(1)),
            _segment_city(plan, swap.group(2)),
        )
        if first and second and first != second:
            names = [s.destination.name for s in plan.segments]
            i, j = names.index(first), names.index(second)
            names[i], names[j] = names[j], names[i]
            return RouteChange(op="reorder", order=names)
    nights = re.search(
        rf"\b(\d{{1,2}})\s+nights?\s+(?:in|at|for)\s+(?:the\s+)?({_NAME})\b",
        text,
        re.IGNORECASE,
    )
    if nights and _segment_city(plan, nights.group(2)):
        return RouteChange(
            op="nights",
            city=_segment_city(plan, nights.group(2)),
            nights=int(nights.group(1)),
        )
    remove = re.search(
        rf"\b(?:remove|drop|skip|cut|delete)\s+(?:the\s+)?({_NAME}?)"
        r"(?:\s+(?:from|out of)\s+(?:the\s+|my\s+|our\s+)?(?:trip|plan|itinerary|route)|\s+altogether|\s+entirely)?\s*[.!?]*$",
        text,
        re.IGNORECASE,
    )
    if remove and _segment_city(plan, remove.group(1)):
        return RouteChange(op="remove", city=_segment_city(plan, remove.group(1)))
    add = re.search(
        rf"\badd\s+(?:the\s+)?({_NAME}?)(?:\s+for\s+(\d{{1,2}})\s+nights?)?"
        rf"(?:\s+(?:after|following)\s+(?:the\s+)?({_NAME}?))?"
        r"(?:\s+(?:to|in|into)\s+(?:the\s+|my\s+|our\s+)?(?:trip|plan|itinerary|route))?\s*[.!?]*$",
        text,
        re.IGNORECASE,
    )
    if add:
        city, count, after = add.group(1).strip(), add.group(2), add.group(3)
        # A city, not a stop: a place the app knows as a city, or one said with its nights.
        if city.casefold() in _known_cities() or count:
            anchor = _segment_city(plan, after) if after else None
            if after and anchor is None:
                return None
            return RouteChange(
                op="add",
                city=city.title() if city.islower() else city,
                after=anchor,
                nights=int(count) if count else None,
            )
    return None


def rule_split_stay(message: str, plan: TripPlan) -> SplitStay | None:
    """ "A different hotel for the last two nights (in Osaka)": the stay of that city gets a second hotel."""
    lower = message.lower()
    found = re.search(
        r"\b(?:different|another|other|new|second)\s+(?:hotel|stay)\s+for\s+the\s+last\s+(\d{1,2}|one|two|three|four|five)\s+nights?\b",
        lower,
    )
    if not found:
        return None
    token = found.group(1)
    count = int(token) if token.isdigit() else WORD_NUMBERS[token]
    cities = _mentioned_cities(plan, message)
    return SplitStay(city=cities[0] if len(cities) == 1 else None, last_nights=count)


def _names(values: list[str]) -> list[str]:
    return [" ".join(value.split()).casefold() for value in values]


def route_differs(plan: TripPlan, context: TripContext) -> bool:
    """The trip form names other cities, or other nights for them, than the plan visits (the page's "generate again"
    after the cities were changed there). A form with no city says nothing about the route."""
    route = context.route
    if not route:
        return False
    planned = [s.destination.name for s in plan.segments]
    if _names([stop.city for stop in route]) != _names(planned):
        return True
    return any(
        stop.nights is not None and stop.nights != segment.dates.night_count
        for stop, segment in zip(route, plan.segments)
        if len(route) > 1
    )


def shares_a_city(plan: TripPlan, context: TripContext) -> bool:
    """At least one city of the form is a city of the plan: a change of route, not a different trip."""
    planned = set(_names([s.destination.name for s in plan.segments]))
    return any(name in planned for name in _names([stop.city for stop in context.route]))


def sync_route(context: TripContext, plan: TripPlan) -> bool:
    """Make the trip form's route the plan's. True when it changed."""
    stops = (
        [
            TripStop(city=s.destination.name, nights=s.dates.night_count)
            for s in plan.segments
        ]
        if len(plan.segments) > 1
        else []
    )
    destination = plan.segments[0].destination.name
    before = (context.stops, context.destination, context.one_way)
    one_way = context.one_way and len(plan.segments) > 1
    context.stops, context.destination, context.one_way = stops, destination, one_way
    return before != (stops, destination, one_way)
