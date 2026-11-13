"""Edits by chat on a plan of several cities.

An edit is either about the route (a city added, dropped, moved or given other nights), about a stay (a second hotel for
the last nights of a city), or about what is inside one city (or all of them). A route change keeps every city whose
name and dates are the same as before, exactly as it was, and plans only the cities that are new or whose dates moved;
the legs it touches are looked up again and the others keep the traveller's choice.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from app.application.edit_scope import segment_for_edit, which_visit_question
from app.application.modification import EditQuestion, EditResult
from app.application.multi_city import build_trip
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
from app.application.stitch import city_context, split
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


async def edit_cities(modify: ModifyPath, *, plan: TripPlan, **_: Any) -> EditResult:
    unchanged = plan.model_copy(deep=True)
    unchanged.change_summary = (
        "I can't change a multi-city plan from chat yet. Use the options on each stay and each leg, "
        "or generate the itinerary again."
    )
    return EditResult(unchanged, [])
