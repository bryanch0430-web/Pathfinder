"""Edits by chat on a plan of several cities.

An edit is either about the route (a city added, dropped, moved or given other nights), about a stay (a second hotel for
the last nights of a city), or about what is inside one city (or all of them). A route change keeps every city whose
name and dates are the same as before, exactly as it was, and plans only the cities that are new or whose dates moved;
the legs it touches are looked up again and the others keep the traveller's choice.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from app.application.edit_scope import segment_for_edit, which_visit_question
from app.application.modification import EditQuestion, EditResult
from app.application.multi_city import build_trip, finish_trip
from app.application.planner import Planner, annotate_stays
from app.application.route_edit import (
    RouteChange,
    SplitStay,
    apply_route_change,
    rule_route_change,
    rule_split_stay,
)
from app.application.route_plan import RouteQuestion, build_segments
from app.application.search_cache import CachedToolbox, SearchCache
from app.application.stitch import city_context, split, stitch
from app.application.validation import diff_plans
from app.domain.errors import CanaryLeak
from app.domain.models import (
    AgentName,
    DateRange,
    Destination,
    TicketOption,
    TripContext,
    TripPlan,
    utcnow,
)

if TYPE_CHECKING:
    from app.application.modification import ModifyPath

logger = logging.getLogger(__name__)

SWITCHED_FLIGHTS = "Your earlier flight choice no longer fits the route, so the flights were looked up again."


def _asks(plan: TripPlan, question: str, message: str) -> EditResult:
    unchanged = plan.model_copy(deep=True)
    unchanged.change_summary = question
    return EditResult(
        unchanged, [], asks=EditQuestion(question, fact="route", request=message)
    )


def _cached(toolbox: Any, session_id: str) -> Any:
    return CachedToolbox(
        toolbox, SearchCache(getattr(toolbox, "store", None), session_id)
    )


async def edit_multi_city(
    modify: ModifyPath,
    *,
    plan: TripPlan,
    context: TripContext,
    message: str,
    route_parts: list[str],
    toolbox: Any,
    tracer: Any,
    emit: Any,
    memory_summaries: list[str] | None = None,
    scope_item_id: str | None = None,
    history: Any = (),
) -> EditResult:
    """The edit of a plan of several cities."""
    draft = await modify._extract(
        message,
        plan,
        context,
        route_parts,
        history=history,
        scope_item_id=scope_item_id,
    )
    stay_split = draft.split_stay or rule_split_stay(message, plan)
    # "a different hotel for the last 2 nights in Osaka" is about the stay, not about Osaka's nights.
    change = draft.route_change or (None if stay_split is not None else rule_route_change(message, plan))
    if change is not None:
        return await edit_route(
            modify,
            plan=plan,
            context=context,
            change=change,
            message=message,
            toolbox=toolbox,
            tracer=tracer,
            emit=emit,
        )
    if stay_split is not None:
        return await split_stay(
            modify,
            plan=plan,
            context=context,
            split=stay_split,
            message=message,
            scope_item_id=scope_item_id,
            toolbox=toolbox,
            tracer=tracer,
            emit=emit,
        )
    return await edit_cities(
        modify,
        plan=plan,
        context=context,
        message=message,
        draft=draft,
        route_parts=route_parts,
        toolbox=toolbox,
        tracer=tracer,
        emit=emit,
        memory_summaries=memory_summaries,
        scope_item_id=scope_item_id,
        history=history,
    )


# -- the route ---------------------------------------------------------------------------------------------------------


def _kept_options(
    old: TripPlan, legs: list, segments_changed: bool
) -> dict[str, list[TicketOption]]:
    """The options of the legs that run between the same places on the same day as before, under the new leg's id."""
    if segments_changed:
        return {}
    kept: dict[str, list[TicketOption]] = {}
    for leg in legs:
        before = next(
            (
                o
                for o in old.legs
                if (o.kind, o.from_place, o.to_place, o.depart_date)
                == (leg.kind, leg.from_place, leg.to_place, leg.depart_date)
            ),
            None,
        )
        if before is None:
            continue
        options = old.options_for(before.id)
        if options:
            kept[leg.id] = [
                option.model_copy(
                    update={
                        "leg_id": leg.id,
                        "id": option.id.replace(before.id, leg.id, 1)
                        if option.id.startswith(before.id)
                        else f"{leg.id}_{option.id}",
                    }
                )
                for option in options
            ]
    return kept


async def edit_route(
    modify: ModifyPath,
    *,
    plan: TripPlan,
    context: TripContext,
    change: RouteChange,
    message: str,
    toolbox: Any,
    tracer: Any,
    emit: Any,
) -> EditResult:
    """A city added, dropped, moved or given other nights; the trip keeps its dates."""
    try:
        stops, sentence = apply_route_change(plan, change)
    except RouteQuestion as asked:
        return _asks(plan, asked.question, message)
    path = modify.plan_path
    toolbox = _cached(toolbox, plan.session_id)
    known = {
        segment.destination.name.casefold(): segment.destination
        for segment in plan.segments
    }
    places: list[Destination] = []
    for stop in stops:
        place = known.get(stop.city.casefold())
        if place is None:
            found = await path._locate(stop.city, toolbox=toolbox, tracer=tracer)
            place = Destination(
                name=stop.city,
                country_code=found.country_code,
                geo=found.geo,
                timezone=found.timezone,
            )
            known[stop.city.casefold()] = place
        places.append(place)
    segments = build_segments(
        places, [stop.nights or 1 for stop in stops], plan.dates.start
    )

    old_parts = split(plan)
    kept_parts: dict[int, TripPlan] = {}
    carry: dict[int, TripPlan] = {}
    taken: set[int] = set()
    for index, segment in enumerate(segments):
        for old_index, old in enumerate(plan.segments):
            if (
                old_index in taken
                or old.destination.name.casefold()
                != segment.destination.name.casefold()
            ):
                continue
            if old.dates == segment.dates:
                kept_parts[index] = old_parts[old_index]
            else:
                carry[index] = old_parts[old_index]
            taken.add(old_index)
            break
    crossing = (len(plan.segments) == 1) != (len(segments) == 1)
    new_legs = None
    from app.application.transport_legs import build_legs

    new_legs = (
        build_legs(segments, context.origin, context.one_way)
        if len(segments) > 1
        else []
    )
    kept_options = _kept_options(plan, new_legs, crossing)
    if crossing:
        sentence = f"{sentence} {SWITCHED_FLIGHTS}"

    async def finish(index: int, part: TripPlan) -> TripPlan:
        old = carry.get(index)
        if old is None:
            return part
        try:
            return await modify.carry_protected(
                old,
                part,
                context=city_context(
                    context,
                    segments[index],
                    last=index == len(segments) - 1,
                    budget=None,
                ),
                toolbox=toolbox,
            )
        except CanaryLeak:
            raise
        except Exception as exc:  # noqa: BLE001 - a stop that cannot be put back is lost, not the whole edit
            logger.warning(
                "keeping the protected stops of %s failed: %s",
                segments[index].destination.name,
                type(exc).__name__,
            )
            return part

    updated = await build_trip(
        path,
        context=context,
        session_id=plan.session_id,
        toolbox=toolbox,
        tracer=tracer,
        emit=emit,
        memory_summaries=None,
        version=plan.version + 1,
        trip_id=plan.trip_id,
        change_summary=sentence,
        segments=segments,
        kept_parts=kept_parts,
        kept_options=kept_options,
        finish_part=finish,
    )
    updated.created_at = plan.created_at
    updated.updated_at = utcnow()
    return EditResult(updated, diff_plans(plan, updated), scope="route")


# -- a second hotel for the last nights of a city -------------------------------------------------------------------


async def split_stay(
    modify: ModifyPath,
    *,
    plan: TripPlan,
    context: TripContext,
    split: SplitStay,
    message: str,
    scope_item_id: str | None,
    toolbox: Any,
    tracer: Any,
    emit: Any,
) -> EditResult:
    """The stay of a city becomes two: its first nights stay as they are and the last ones go to another hotel."""
    named = None
    if split.city:
        hits = [
            s
            for s in plan.segments
            if s.destination.name.casefold() == split.city.casefold()
        ]
        named = hits[0] if len(hits) == 1 else None
        if len(hits) > 1:
            return _asks(plan, which_visit_question(plan, split.city), message)
    else:
        found = segment_for_edit(plan, message, scope_item_id, None)
        if found == "ask":
            return _asks(plan, which_visit_question(plan, message), message)
        named = None if found == "all" else found
    if named is None:
        return _asks(plan, "Which city should get the other hotel?", message)
    stays = plan.stays_in(named.id)
    city = named.destination.name
    if not stays:
        return _asks(
            plan,
            f"There is no stay in {city} yet to split. Choose a hotel there first.",
            message,
        )
    stay = stays[-1]
    total = (stay.check_out - stay.check_in).days
    if split.last_nights >= total:
        return _asks(
            plan,
            f"The stay in {city} has {total} night{'s' if total != 1 else ''}; a second hotel for the last "
            f"{split.last_nights} would leave none for {stay.name}. How many nights should it get?",
            message,
        )
    when = stay.check_out - timedelta(days=split.last_nights)
    rest = DateRange(start=when, end=stay.check_out)
    toolbox = _cached(toolbox, plan.session_id)
    ctx = city_context(context, named, last=named is plan.segments[-1], budget=None)
    results = await modify.plan_path.run_agents(
        context=ctx,
        destination=named.destination,
        date_range=rest,
        toolbox=toolbox,
        emit=emit,
        names={AgentName.HOTEL},
    )
    rows = results[AgentName.HOTEL.value].payload.get("hotels") or []
    options = Planner.hotel_options(rows, None, rest)
    other = next(
        (o for o in options if o.name != stay.name and o.price_per_night is not None),
        None,
    ) or next((o for o in options if o.name != stay.name), None)
    if other is None:
        unchanged = plan.model_copy(deep=True)
        unchanged.change_summary = f"No other hotel was found in {city} for those nights, so the stay is as it was."
        return EditResult(unchanged, [])
    updated = plan.model_copy(deep=True)
    second = other.model_copy(
        update={
            "check_in": when,
            "check_out": stay.check_out,
            "segment_id": named.id,
            "confirmed": False,
            "locked": False,
        }
    )
    first = next(s for s in updated.hotels if s.id == stay.id)
    first.check_out = when
    updated.hotels = sorted([*updated.hotels, second], key=lambda s: s.check_in)
    known = {s.id for s in updated.hotel_options}
    updated.hotel_options += [
        o.model_copy(update={"segment_id": named.id})
        for o in options
        if o.id not in known
    ]
    updated.cost = modify.planner.cost_for_plan(context, updated)
    annotate_stays(updated)
    updated.version = plan.version + 1
    updated.updated_at = utcnow()
    updated.change_summary = (
        f"{stay.name} keeps the first {total - split.last_nights} night{'s' if total - split.last_nights != 1 else ''} in "
        f"{city}; the last {split.last_nights} go to {second.name}."
    )
    return EditResult(updated, diff_plans(plan, updated))


# -- what is inside the cities ----------------------------------------------------------------------------------------

DATES_FROM_CHAT = (
    "The dates of a trip of several cities are the sum of its cities' nights. Change a city's nights, add or drop a "
    "city, or change the trip's dates in the trip details."
)
_DAY_NAMED = re.compile(r"\bday\s*(\d{1,2})\b|\b(\d{1,2})(?:st|nd|rd|th)\s+day\b", re.IGNORECASE)


def _localise_day(message: str, plan: TripPlan, segment_index: int) -> str:
    """The message with the day it names counted from the first day of the city (the city is edited as a plan of its
    own, whose first day is day 1)."""
    first = next(i for i, day in enumerate(plan.days) if day.segment_id == plan.segments[segment_index].id)

    def local(found: re.Match[str]) -> str:
        number = int(found.group(1) or found.group(2))
        return f"day {max(number - first, 1)}"

    return _DAY_NAMED.sub(local, message, count=1)


def _own_id(item_id: str | None, number: int) -> str | None:
    """A selected stop's or stay's id as the city's own plan names it (the cities after the first carry a prefix)."""
    prefix = f"s{number}_"
    return item_id[len(prefix) :] if item_id and number > 1 and item_id.startswith(prefix) else item_id


async def edit_cities(
    modify: ModifyPath,
    *,
    plan: TripPlan,
    context: TripContext,
    message: str,
    draft: Any,
    route_parts: list[str],
    toolbox: Any,
    tracer: Any,
    emit: Any,
    memory_summaries: list[str] | None = None,
    scope_item_id: str | None = None,
    history: Any = (),
) -> EditResult:
    """An edit of what is inside one city, or inside all of them when it names none: each city is edited as a plan of its
    own with the ordinary edit, and the cities are put together again. A city the edit does not touch stays as it was."""
    target = segment_for_edit(plan, message, scope_item_id, draft.target_day)
    if target == "ask":
        return _asks(plan, which_visit_question(plan, message), message)
    if target == "all":
        indexes = list(range(len(plan.segments)))
    else:
        indexes = [next(i for i, s in enumerate(plan.segments) if s.id == target.id)]
    parts = split(plan)
    toolbox = _cached(toolbox, plan.session_id)
    gate = asyncio.Semaphore(3)
    total = max(plan.dates.night_count, 1)

    async def one(index: int) -> EditResult:
        segment = plan.segments[index]
        share = None if context.budget is None else round(context.budget * segment.dates.night_count / total, 2)
        city = city_context(context, segment, last=index == len(plan.segments) - 1, budget=share)
        asked = message if target == "all" else _localise_day(message, plan, index)
        async with gate:
            return await modify.run(
                plan=parts[index],
                context=city,
                message=asked,
                route_parts=route_parts,
                toolbox=toolbox,
                tracer=tracer,
                emit=emit,
                memory_summaries=memory_summaries,
                scope_item_id=_own_id(scope_item_id, index + 1),
                history=history,
            )

    results = await asyncio.gather(*(one(i) for i in indexes))
    for result in results:
        if result.asks is not None:
            return _asks(plan, result.asks.question, message)
    changed = [
        (i, result[0]) for i, result in zip(indexes, results) if result[0].version != parts[i].version
    ]
    if not changed:
        unchanged = plan.model_copy(deep=True)
        unchanged.change_summary = next((r[0].change_summary for r in results if r[0].change_summary), "") or (
            "Nothing in the plan matched that request."
        )
        return EditResult(unchanged, [])
    if any(part.dates != parts[i].dates for i, part in changed):
        unchanged = plan.model_copy(deep=True)
        unchanged.change_summary = DATES_FROM_CHAT
        return EditResult(unchanged, [])
    for i, part in changed:
        parts[i] = part
    touched = {plan.segments[i].id for i, _ in changed}

    stitched = stitch(
        parts,
        plan.segments,
        plan.legs,
        plan.tickets,
        context,
        session_id=plan.session_id,
        version=plan.version + 1,
        trip_id=plan.trip_id,
    )
    updated = _graft_untouched(plan, stitched, touched)
    updated = await finish_trip(modify.plan_path, updated, parts, context=context, toolbox=toolbox, emit=emit, assess=False)
    updated.assessment = plan.assessment
    updated.summary = plan.summary
    updated.created_at = plan.created_at
    updated.updated_at = utcnow()
    updated.change_summary = " ".join(
        dict.fromkeys(part.change_summary for _, part in changed if part.change_summary)
    )
    return EditResult(
        updated,
        diff_plans(plan, updated),
        scope="plan" if target == "all" else "stop",
        selected=bool(scope_item_id),
    )


def _graft_untouched(old: TripPlan, stitched: TripPlan, touched: set[str]) -> TripPlan:
    """The stitched plan with every city the edit did not touch exactly as it was (days, stays, notes), so putting the
    cities together again changes nothing in them."""
    for index, day in enumerate(stitched.days):
        if day.segment_id not in touched:
            before = next((d for d in old.days if d.date == day.date), None)
            if before is not None:
                stitched.days[index] = before.model_copy(deep=True)
    kept = [stay.model_copy(deep=True) for stay in old.hotels if stay.segment_id not in touched]
    changed = [stay for stay in stitched.hotels if stay.segment_id in touched]
    stitched.hotels = sorted([*kept, *changed], key=lambda stay: stay.check_in)
    stitched.hotel_options = [
        *[o.model_copy(deep=True) for o in old.hotel_options if o.segment_id not in touched],
        *[o for o in stitched.hotel_options if o.segment_id in touched],
    ]
    stitched.data_quality.notes = list(dict.fromkeys([*old.data_quality.notes, *stitched.data_quality.notes]))[:30]
    return stitched
