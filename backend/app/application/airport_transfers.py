"""Airport transfers: how to get between an airport and a city's stay when the traveller flies.

A transfer is derived from the plan, never planned on its own. For every hop whose chosen option is a flight there is a
``to_stay`` transfer in the city flown into (the airport to that night's stay) and a ``from_stay`` transfer in the city
flown out of (the night before's stay to the airport). The traveller's home end gets none. A transfer's options are
estimates (no timetable, no fare): transit from the routes source when it has a route, a bus or shuttle worked from the
drive, and a taxi. They are chosen and locked like any other leg.

``sync_transfers`` recomputes them from the plan; a transfer whose airport and stay did not change keeps its chosen option
and its lock, one that did loses its lock and is told.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from app.application.stitch import (
    ARRIVAL_BUFFER_MINUTES,
    TRANSIT_PREFIX,
    _clock,
    _minutes,
    _span,
    _transit_item,
    _without,
)
from app.application.transport_legs import (
    BUS_DRIVE_FACTOR,
    ESTIMATE_DEPARTS,
    _directions_link,
    _routed,
    ground_mode,
    rank_options,
)
from app.domain.models import (
    DataStatus,
    DayPlan,
    GeoPoint,
    HotelStay,
    RouteLeg,
    TicketOption,
    TransportLeg,
    TripPlan,
    selected_ticket,
)
from app.infrastructure.providers.flights import AIRPORTS
from app.infrastructure.providers.routes import estimate_leg

logger = logging.getLogger(__name__)

TO_STAY, FROM_STAY = "to_stay", "from_stay"
#: A transfer from the airport leaves this long after the flight lands.
LAND_MINUTES = 45
#: A transfer to the airport is timed to arrive this long before the flight leaves.
AIRPORT_BEFORE_MINUTES = 120
_IATA = re.compile(r"^[A-Z]{3}$")
_NAMES = {
    "train": "Airport train (estimate)",
    "bus": "Airport bus (estimate)",
    "shuttle": "Airport shuttle (estimate)",
}
NOTE = "Estimate: no timetable or fare was looked up."


@dataclass
class _Want:
    """One transfer the plan should have."""

    kind: str
    segment_id: str
    airport: str
    stay: HotelStay
    day: date
    at: datetime | None  # when the flight lands (to_stay) or leaves (from_stay)

    @property
    def leg_id(self) -> str:
        return f"{self.kind}_{self.segment_id}"


_KNOWN = frozenset(code for codes in AIRPORTS.values() for code in codes)


def _code(value: str) -> str:
    """An airport code the flight source works from (its own table of cities and airports); anything else, a city name
    or a code that table does not hold, is no airport to look up."""
    text = (value or "").strip().upper()
    return text if _IATA.match(text) and text in _KNOWN else ""


def _segment_named(
    plan: TripPlan, name: str, *, starts: date | None = None, ends: date | None = None
):
    for segment in plan.segments:
        if segment.destination.name != name:
            continue
        if starts is not None and segment.dates.start != starts:
            continue
        if ends is not None and segment.dates.end != ends:
            continue
        return segment
    return None


def _stay_of(plan: TripPlan, segment_id: str, day: date) -> HotelStay | None:
    stay = plan.stay_on(day)
    if stay is not None and stay.segment_id in ("", segment_id):
        return stay
    stays = plan.stays_in(segment_id)
    return stays[0] if stays else None


def _wants(plan: TripPlan) -> list[_Want]:
    """The transfers the plan's chosen flights call for."""
    wants: list[_Want] = []
    hops = plan.route_legs()
    for leg in hops:
        option = selected_ticket(plan.options_for(leg.id), leg.id)
        if option is None or option.mode != "flight":
            continue
        if leg.kind in ("outbound", "between"):
            segment = (
                plan.segments[0]
                if leg.kind == "outbound"
                else _segment_named(plan, leg.to_place, starts=leg.depart_date)
            )
            airport = _code(option.destination)
            if segment is not None and airport:
                landed = option.arrive_at
                day = (
                    landed.date()
                    if landed
                    else (leg.depart_date or segment.dates.start)
                )
                stay = _stay_of(plan, segment.id, day)
                if stay is not None:
                    wants.append(_Want(TO_STAY, segment.id, airport, stay, day, landed))
        leaves_from: tuple[Any, str, datetime | None] | None = None
        if leg.kind == "between":
            segment = _segment_named(plan, leg.from_place, ends=leg.depart_date)
            leaves_from = (segment, _code(option.origin), option.depart_at)
        elif leg.kind == "return":
            leaves_from = (plan.segments[-1], _code(option.origin), option.depart_at)
        elif (
            leg.kind == "outbound"
            and len(plan.segments) == 1
            and not any(l.kind == "return" for l in hops)
            and option.return_depart_at is not None
        ):
            # A round-trip fare carries the flight home on the same option.
            leaves_from = (
                plan.segments[0],
                _code(option.return_origin) or _code(option.destination),
                option.return_depart_at,
            )
        if leaves_from is not None:
            segment, airport, leaves = leaves_from
            if segment is not None and airport:
                day = (
                    leaves.date() if leaves else (leg.depart_date or segment.dates.end)
                )
                stay = _stay_of(plan, segment.id, day - timedelta(days=1))
                if stay is not None:
                    wants.append(
                        _Want(FROM_STAY, segment.id, airport, stay, day, leaves)
                    )
    unique: dict[str, _Want] = {}
    for want in wants:
        unique.setdefault(want.leg_id, want)
    return sorted(unique.values(), key=lambda w: (w.day, w.kind != TO_STAY))


async def _locate(
    code: str, *, registry: Any, toolbox: Any, cache: Any
) -> GeoPoint | None:
    """Where an airport is: "<IATA> airport" through the geocoder, kept for the session. Not found is no point; the
    coordinates are never made up."""
    key = (code.upper(),)
    kept = cache.get("airports", key) if cache is not None else None
    if kept is not None:
        return GeoPoint(**kept[0]) if kept else None
    try:
        found = await toolbox.call(
            "geocode", registry.geocoder.geocode, name=f"{code} airport"
        )
    except Exception:  # noqa: BLE001 - no result, or the lookup was refused: no transfer for that airport
        return None
    point = getattr(found, "geo", None) if found else None
    if cache is not None:
        cache.put("airports", key, [point.model_dump()] if point is not None else [])
    return point


async def _routes(
    origin: GeoPoint, destination: GeoPoint, *, registry: Any, toolbox: Any
) -> tuple[RouteLeg | None, RouteLeg | None]:
    """The transit and the drive between two points from the routes sources, as the legs between cities are looked up."""
    transit: RouteLeg | None = None
    driving: RouteLeg | None = None
    for source in getattr(registry, "routes", []):
        if not (getattr(source, "available", True) and hasattr(source, "leg")):
            continue
        try:
            transit = transit or await toolbox.call(
                "routes.leg",
                source.leg,
                origin=origin,
                destination=destination,
                mode="transit",
            )
            driving = driving or await toolbox.call(
                "routes.leg",
                source.leg,
                origin=origin,
                destination=destination,
                mode="drive",
            )
        except Exception:  # noqa: BLE001, S112 - a source that fails leaves the transfer to the others
            continue
        if transit is not None and driving is not None:
            break
    return transit, driving


def _option(
    want: _Want,
    leg_id: str,
    mode: str,
    name: str,
    minutes: int,
    from_text: str,
    to_text: str,
    travel_mode: str,
) -> TicketOption:
    if want.kind == TO_STAY:
        depart = (
            want.at + timedelta(minutes=LAND_MINUTES)
            if want.at
            else datetime.combine(want.day, time.fromisoformat(ESTIMATE_DEPARTS))
        )
        arrive = depart + timedelta(minutes=minutes)
    else:
        arrive = (
            want.at - timedelta(minutes=AIRPORT_BEFORE_MINUTES) if want.at else None
        )
        depart = (
            arrive - timedelta(minutes=minutes)
            if arrive
            else datetime.combine(want.day, time.fromisoformat(ESTIMATE_DEPARTS))
        )
        if arrive is None:
            arrive = depart + timedelta(minutes=minutes)
    return TicketOption(
        id=f"{leg_id}_{mode}",
        leg_id=leg_id,
        mode=mode,
        name=name,
        origin=from_text,
        destination=to_text,
        depart_at=depart,
        arrive_at=arrive,
        duration_minutes=minutes,
        price=None,
        estimated=True,
        booking_url=_directions_link(from_text, to_text, travel_mode),
        notes=NOTE,
        data_status=DataStatus.OK,
    )


async def _options_for(
    want: _Want, point: GeoPoint, *, registry: Any, toolbox: Any
) -> list[TicketOption]:
    """Transit if the routes source has it, a bus or shuttle from the drive, and a taxi; the fastest first."""
    stay_geo = want.stay.geo
    assert stay_geo is not None
    start, end = (point, stay_geo) if want.kind == TO_STAY else (stay_geo, point)
    from_text, to_text = (
        (f"{want.airport} airport", want.stay.name)
        if want.kind == TO_STAY
        else (want.stay.name, f"{want.airport} airport")
    )
    transit, driving = await _routes(start, end, registry=registry, toolbox=toolbox)
    drive = (
        driving
        if driving is not None and _routed(driving) and driving.duration_minutes
        else estimate_leg(start, end, "drive")
    )
    drive_minutes = int(drive.duration_minutes or 0)
    options: list[TicketOption] = []
    if transit is not None and _routed(transit) and transit.duration_minutes:
        mode = ground_mode(transit)
        if mode in _NAMES:
            options.append(
                _option(
                    want,
                    want.leg_id,
                    mode,
                    _NAMES[mode],
                    int(transit.duration_minutes),
                    from_text,
                    to_text,
                    "transit",
                )
            )
    if not any(o.mode in ("bus", "shuttle") for o in options):
        options.append(
            _option(
                want,
                want.leg_id,
                "bus",
                "Airport bus or shuttle (estimate)",
                round(drive_minutes * BUS_DRIVE_FACTOR),
                from_text,
                to_text,
                "transit",
            )
        )
    options.append(
        _option(
            want,
            want.leg_id,
            "taxi",
            "Taxi or ride (estimate)",
            drive_minutes,
            from_text,
            to_text,
            "driving",
        )
    )
    return rank_options(options)


# -- the day ------------------------------------------------------------------------------------------------------------


def _drop_old_items(plan: TripPlan) -> None:
    prefixes = (f"{TRANSIT_PREFIX}{TO_STAY}_", f"{TRANSIT_PREFIX}{FROM_STAY}_")
    for index, day in enumerate(plan.days):
        if any(item.id.startswith(prefixes) for item in day.items):
            plan.days[index] = day.model_copy(
                update={
                    "items": [i for i in day.items if not i.id.startswith(prefixes)]
                }
            )


def _place_arrival(
    day: DayPlan, leg: TransportLeg, option: TicketOption, notes: list[str]
) -> DayPlan:
    """The airport-to-stay transfer on its day, and the stops it would run into taken out."""
    start, end = _span(option)
    cutoff = end + ARRIVAL_BUFFER_MINUTES
    dropped = [
        item
        for item in day.items
        if not (item.locked or item.confirmed)
        and (
            _minutes(item.start) < cutoff
            or (_minutes(item.start) < end and _minutes(item.end) > start)
        )
    ]
    kept = [item for item in day.items if item not in dropped]
    notes.extend(
        f"{item.name} was left out of {day.date.isoformat()}: you reach your stay at {_clock(end)}."
        for item in dropped
    )
    if any(_minutes(item.start) < end and _minutes(item.end) > start for item in kept):
        notes.append(
            f"{leg.from_place} → {leg.to_place} could not be placed on {day.date.isoformat()}: a fixed stop is in the way."
        )
        return _without(day, dropped, kept)
    return _without(day, dropped, [_transit_item(leg, option, start, end), *kept])


def _place_departure(
    day: DayPlan, leg: TransportLeg, option: TicketOption, notes: list[str]
) -> DayPlan:
    """The stay-to-airport transfer on its day, and the stops that run into it taken out."""
    start, end = _span(option)
    dropped = [
        item
        for item in day.items
        if not (item.locked or item.confirmed) and _minutes(item.end) > start
    ]
    kept = [item for item in day.items if item not in dropped]
    notes.extend(
        f"{item.name} was left out of {day.date.isoformat()}: you leave for the airport at {_clock(start)}."
        for item in dropped
    )
    if any(_minutes(item.end) > start and _minutes(item.start) < end for item in kept):
        notes.append(
            f"{leg.from_place} → {leg.to_place} could not be placed on {day.date.isoformat()}: a fixed stop is in the way."
        )
        return _without(day, dropped, kept)
    return _without(day, dropped, [_transit_item(leg, option, start, end), *kept])


def _put_on_days(plan: TripPlan, legs: list[TransportLeg]) -> None:
    notes: list[str] = []
    for leg in legs:
        option = selected_ticket(plan.options_for(leg.id), leg.id)
        if option is None or option.depart_at is None:
            continue
        day_date = option.depart_at.date()
        if leg.kind == FROM_STAY:
            day_date = option.arrive_at.date() if option.arrive_at else day_date
        index = next((i for i, d in enumerate(plan.days) if d.date == day_date), None)
        if index is None:
            continue
        place = _place_arrival if leg.kind == TO_STAY else _place_departure
        plan.days[index] = place(plan.days[index], leg, option, notes)
    if notes:
        plan.data_quality.notes = list(
            dict.fromkeys([*plan.data_quality.notes, *notes])
        )[:30]


# -- the whole -----------------------------------------------------------------------------------------------------------


async def sync_transfers(
    plan: TripPlan, *, registry: Any, toolbox: Any, cache: Any = None
) -> list[str]:
    """Make the plan's airport transfers what its flights and stays call for. The plan is changed in place; the sentences
    returned say which transfers were worked out again because their flight or stay changed."""
    old_legs = {leg.id: leg for leg in plan.transfer_legs()}
    old_chosen = {
        leg_id: next((o.id for o in plan.options_for(leg_id) if o.selected), None)
        for leg_id in old_legs
    }
    legs: list[TransportLeg] = []
    tickets: list[TicketOption] = []
    said: list[str] = []
    for want in _wants(plan):
        if want.stay.geo is None:
            continue
        point = await _locate(
            want.airport, registry=registry, toolbox=toolbox, cache=cache
        )
        if point is None:
            continue
        options = await _options_for(want, point, registry=registry, toolbox=toolbox)
        from_text, to_text = (
            (f"{want.airport} airport", want.stay.name)
            if want.kind == TO_STAY
            else (want.stay.name, f"{want.airport} airport")
        )
        leg = TransportLeg(
            id=want.leg_id,
            from_place=from_text,
            to_place=to_text,
            depart_date=want.day,
            kind=want.kind,  # type: ignore[arg-type]
            segment_id=want.segment_id,
            airport=want.airport,
        )
        prior = old_legs.get(leg.id)
        keep = None
        if prior is not None:
            if (prior.airport, prior.from_place, prior.to_place) == (
                leg.airport,
                leg.from_place,
                leg.to_place,
            ):
                leg.locked = prior.locked
                keep = old_chosen.get(leg.id)
            else:
                city = next(
                    (
                        s.destination.name
                        for s in plan.segments
                        if s.id == want.segment_id
                    ),
                    "",
                )
                new = (
                    "stay"
                    if (prior.to_place if want.kind == TO_STAY else prior.from_place)
                    != want.stay.name
                    else "flight"
                )
                said.append(
                    f"The airport transfer in {city} was worked out again for your new {new}."
                )
        chosen = next((o.id for o in options if o.id == keep), options[0].id)
        tickets += [o.model_copy(update={"selected": o.id == chosen}) for o in options]
        legs.append(leg)
    plan.transfers_synced = True
    plan.legs = [*plan.route_legs(), *legs]
    plan.tickets = [t for t in plan.tickets if t.leg_id not in old_legs] + tickets
    _drop_old_items(plan)
    _put_on_days(plan, legs)
    return said


async def sync_session_transfers(
    plan: TripPlan, *, registry: Any, store: Any, session_id: str, before: TripPlan | None = None
) -> list[str]:
    """``sync_transfers`` for a turn or a choice: its own toolbox, the session's search cache. ``before`` is the plan the
    edit started from (None for a new plan, which always gets its transfers); a plan stored before transfers existed
    is left without them. The transfers are derived data, so a failure
    here is logged and never costs the plan or the turn; nothing is said then."""
    if before is not None and not before.transfers_synced:
        return []
    from app.application.search_cache import SearchCache
    from app.application.toolbox import Toolbox
    from app.domain.models import new_id
    from app.infrastructure.observability import NoopTracer

    try:
        toolbox = Toolbox(path="modify", tracer=NoopTracer(), store=store, request_id=new_id("xfer"))
        return await sync_transfers(plan, registry=registry, toolbox=toolbox, cache=SearchCache(store, session_id))
    except Exception as exc:  # noqa: BLE001 - derived data: never costs the plan
        logger.warning("airport transfers could not be worked out: %s", type(exc).__name__)
        return []
