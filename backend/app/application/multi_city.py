"""The multi-city branch of the new-trip path: a trip of several cities is planned as one single-city plan per city,
with the existing pipeline, and the plans are put together.

The legs between the cities are looked up first (what they cost comes off the budget before it is shared among the
cities), then the cities are planned three at a time, then the whole is stitched, costed, checked and assessed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.application.assessment import assess_and_improve
from app.application.planner import _constraints, annotate_stays
from app.application.refine import evaluate_plan
from app.application.route_plan import RouteQuestion, allocate_nights, build_segments
from app.application.search_cache import CachedToolbox, SearchCache
from app.application.stitch import city_context, stitch
from app.application.toolbox import Toolbox
from app.application.transport_legs import build_legs, leg_options
from app.application.trip_rules import blocked_dates, travel_windows
from app.application.validation import tidy_data_gaps, validate_plan
from app.domain.errors import CanaryLeak, PlanInvalidError
from app.domain.models import (
    AgentName,
    AgentResult,
    DateRange,
    Destination,
    TicketOption,
    TransportLeg,
    TripContext,
    TripPlan,
    TripSegment,
)
from app.domain.ports import Tracer

if TYPE_CHECKING:
    from app.application.planning import PlanPath

logger = logging.getLogger(__name__)

#: Cities planned, and legs looked up, at the same time.
PARALLEL = 3
#: Errors of the whole-plan checks that a missing source explains rather than a bug: they become plan notes.
_SOFT_ERRORS = ("no stay for the night",)


def _key(name: str) -> str:
    return name.strip().casefold()


def _city_emit(
    emit: Callable[[str, dict[str, Any]], None], city: str, index: int, count: int
):
    """The emit of one city's planning: its messages name the city and its progress fills its share of the bar."""

    def send(name: str, data: dict[str, Any]) -> None:
        if name == "progress":
            share = float(data.get("progress", 0)) / 100
            data = {
                **data,
                "city": city,
                "progress": round(10 + (index + share) / count * 75),
                "message": f"{city}: {data.get('message', '')}",
            }
        elif name == "agent":
            data = {**data, "city": city}
        emit(name, data)

    return send


async def _origin(path: PlanPath, name: str, toolbox: Toolbox) -> Destination:
    """The traveller's home as a place; just its name when no source can locate it."""
    try:
        found = await toolbox.call("geocode", path.registry.geocoder.geocode, name=name)
    except Exception:  # noqa: BLE001 - a home the sources cannot find still has a name for the legs
        found = None
    if not found:
        return Destination(name=name)
    return Destination(
        name=name,
        country_code=found.country_code,
        geo=found.geo,
        timezone=found.timezone,
    )


def _ends(
    legs: list[TransportLeg], places: list[Destination], home: Destination | None
) -> list[tuple[Destination, Destination]]:
    """The two places each leg joins, in leg order."""
    pairs: list[tuple[Destination, Destination]] = []
    between = 0
    for leg in legs:
        if leg.kind == "outbound":
            pairs.append((home, places[0]))
        elif leg.kind == "return":
            pairs.append((places[-1], home))
        else:
            pairs.append((places[between], places[between + 1]))
            between += 1
    return pairs


async def plan_route(
    path: PlanPath,
    *,
    context: TripContext,
    session_id: str,
    toolbox: Toolbox,
    tracer: Tracer,
    emit: Callable[[str, dict[str, Any]], None],
    memory_summaries: list[str] | None,
    version: int,
    trip_id: str | None,
    change_summary: str,
) -> TripPlan:
    settings = path.settings
    route = context.route
    if len(route) > settings.max_cities:
        raise PlanInvalidError(
            f"I can plan up to {settings.max_cities} cities in one trip."
        )
    date_range = context.date_range()
    if not date_range:
        raise PlanInvalidError("The trip dates are incomplete.")
    if len(date_range.dates) > settings.max_days:
        raise PlanInvalidError(
            f"Trips longer than {settings.max_days} days are out of scope."
        )
    try:
        nights, note = allocate_nights(route, date_range.night_count)
    except RouteQuestion as asked:
        raise PlanInvalidError(asked.question, retryable=False) from asked

    # A search paid for in this session is not paid for again.
    toolbox = CachedToolbox(toolbox, SearchCache(getattr(toolbox, "store", None), session_id))

    # Every city once, however many times the route visits it.
    located: dict[str, Any] = {}
    for stop in route:
        if _key(stop.city) not in located:
            emit(
                "progress",
                {"phase": "route", "message": f"Locating {stop.city}.", "progress": 8},
            )
            located[_key(stop.city)] = await path._locate(
                stop.city, toolbox=toolbox, tracer=tracer
            )
    places = [
        Destination(
            name=stop.city,
            country_code=located[_key(stop.city)].country_code,
            geo=located[_key(stop.city)].geo,
            timezone=located[_key(stop.city)].timezone,
        )
        for stop in route
    ]
    segments = build_segments(places, nights, date_range.start)
    legs = build_legs(segments, context.origin, context.one_way)
    home = await _origin(path, context.origin, toolbox) if context.origin else None

    gate = asyncio.Semaphore(PARALLEL)

    async def look_up(
        leg: TransportLeg, start: Destination, end: Destination
    ) -> list[TicketOption]:
        async with gate:
            emit(
                "progress",
                {
                    "phase": "legs",
                    "message": f"Looking up {leg.from_place} → {leg.to_place}.",
                    "progress": 10,
                },
            )
            return await leg_options(
                leg,
                start,
                end,
                registry=path.registry,
                toolbox=toolbox,
                party_size=context.party_size or 1,
                currency=context.currency,
            )

    found = await asyncio.gather(
        *(look_up(leg, a, b) for leg, (a, b) in zip(legs, _ends(legs, places, home)))
    )
    tickets = [ticket for options in found for ticket in options]

    # The legs come off the budget first; what is left is shared per night among the cities.
    shares: list[float | None] = [None] * len(segments)
    if context.budget is not None:
        spent = sum(
            path.planner.fx.convert(
                ticket.price.amount, ticket.price.currency, context.currency
            )
            for ticket in tickets
            if ticket.selected and ticket.price is not None
        )
        per_night = max(0.0, context.budget - spent) / max(date_range.night_count, 1)
        shares = [round(per_night * stay, 2) for stay in nights]

    from app.application.planning import AGENT_FACTORIES

    agents = set(AGENT_FACTORIES) - {AgentName.TICKET}
    first_visit: dict[str, asyncio.Future] = {}
    for segment in segments:
        first_visit.setdefault(
            _key(segment.destination.name), asyncio.get_running_loop().create_future()
        )
    plain = [_key(segment.destination.name) for segment in segments]
    owners = [plain.index(key) == index for index, key in enumerate(plain)]

    async def one(index: int, segment: TripSegment) -> TripPlan:
        city = segment.destination.name
        key = _key(city)
        own = owners[index]
        future = first_visit[key]
        given: dict[str, AgentResult] | None = None
        try:
            if not own:
                # A city visited twice is researched for its places once: this visit reuses the first one's.
                earlier = await future
                given = {
                    name: earlier[name] for name in ("attraction",) if name in earlier
                } or None
            async with gate:
                return await path._plan_one(
                    context=city_context(
                        context,
                        segment,
                        last=index == len(segments) - 1,
                        budget=shares[index],
                    ),
                    session_id=session_id,
                    toolbox=toolbox,
                    tracer=tracer,
                    emit=_city_emit(emit, city, index, len(segments)),
                    # The nights the city really has, so the stay, the weather and the stops are looked up for them.
                    date_range=DateRange(
                        start=segment.dates.start, end=segment.dates.end
                    ),
                    memory_summaries=memory_summaries,
                    version=version,
                    trip_id=trip_id,
                    change_summary=change_summary,
                    names=agents,
                    destination=located[key],
                    assess=False,
                    given=given,
                    on_results=(
                        lambda results: future.done() or future.set_result(results)
                    )
                    if own
                    else None,
                )
        except (asyncio.CancelledError, CanaryLeak):
            raise
        except PlanInvalidError as exc:
            raise PlanInvalidError(
                f"{city}: {exc}", retryable=getattr(exc, "retryable", True)
            ) from exc
        except Exception as exc:
            logger.warning("planning %s failed: %s", city, type(exc).__name__)
            raise PlanInvalidError(f"I could not plan {city}.") from exc
        finally:
            if own and not future.done():
                future.set_result({})

    tasks = [
        asyncio.create_task(one(index, segment))
        for index, segment in enumerate(segments)
    ]
    try:
        parts = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    emit(
        "progress",
        {"phase": "planner", "message": "Putting the cities together.", "progress": 88},
    )
    plan = stitch(
        list(parts),
        segments,
        legs,
        tickets,
        context,
        session_id=session_id,
        version=version,
        trip_id=trip_id,
        note=note,
    )
    plan.change_summary = change_summary
    plan.cost = path.planner.cost_for_plan(context, plan)
    rules = [*context.hard_constraints, *context.preferences]
    plan.constraints = _constraints(
        context,
        plan.days,
        plan.first_stay,
        plan.tickets,
        plan.cost,
        blocked=blocked_dates(rules, plan.dates.dates),
        windows=travel_windows(rules, plan.dates.dates),
    )
    seen_checks = {check.text for check in plan.constraints}
    for part in parts:
        plan.constraints += [
            check for check in part.constraints if check.text not in seen_checks
        ]
        seen_checks.update(check.text for check in part.constraints)
    annotate_stays(plan)

    if settings.enable_plan_assessment:
        try:
            plan = await assess_and_improve(
                plan,
                context=context,
                issues=evaluate_plan(plan, context),
                registry=path.registry,
                toolbox=toolbox,
                planner=path.planner,
                settings=settings,
                emit=emit,
            )
        except CanaryLeak:
            raise
        except Exception as exc:  # noqa: BLE001 - the assessment fails open, as it does for one city
            logger.warning("plan assessment failed open: %s", type(exc).__name__)

    errors = validate_plan(plan, settings)
    soft = [error for error in errors if error.startswith(_SOFT_ERRORS)]
    hard = [error for error in errors if error not in soft]
    if hard:
        raise PlanInvalidError(
            "The cities of the trip do not fit together: " + "; ".join(hard[:3])
        )
    plan.data_quality.notes = [*plan.data_quality.notes, *soft]
    plan = tidy_data_gaps(plan)
    emit("progress", {"phase": "ready", "message": "Itinerary ready.", "progress": 100})
    return plan
