"""Transfer times between consecutive stops, from a logged routes tool.

A transfer time on the plan is a fact like a price: it must come from a tool
result in the trip's log (Proposal TC-09, p4). The planner and the edit path ask
the transport-model source for a day's legs through the Toolbox
(``routes.segments``), and each plan item records the leg it used: the tool, the
provider and the leg's id in that result.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.models import DayPlan, GeoPoint, PlanItem, RouteLeg, TransferLeg, TripPlan
from app.infrastructure.http import haversine_km
from app.infrastructure.providers.routes import TRANSPORT_SPEED_KMH, TransportModelRoutes

TRANSFER_TOOL = "routes.segments"
TRANSPORT_NOTE = {
    "walk": "on foot",
    "metro": "by metro or bus",
    "drive": "by car or taxi",
    "flight": "by plane",
}
# Nothing is pushed past this time when a day is re-timed after an edit.
LATEST_END = 23 * 60 + 30

Legs = dict[tuple[str, str], RouteLeg]
_NOTE = re.compile(
    r"\s*About \d+ min [a-z ]+? from the previous stop \((?:estimate|transport model)\)\."
    r"(?: Plus \d+ min for (?:an assumed|the reported) rail delay\.)?"
)


async def fetch_legs(
    stops: list[tuple[str, GeoPoint]], *, toolbox: Any | None, source: Any | None
) -> tuple[Legs, str]:
    """Legs between every ordered pair of ``stops`` (id, geo), and the tool that logged them.

    One Toolbox call per day. Without a toolbox the legs are computed but not
    logged, so the tool name comes back empty and grounding will not accept them.
    """
    if len(stops) < 2:
        return {}, ""
    source = source or TransportModelRoutes()
    ids = [stop_id for stop_id, _ in stops]
    points = [geo for _, geo in stops]
    try:
        if toolbox is not None:
            legs = await toolbox.call(TRANSFER_TOOL, source.segments, points=points, ids=ids)
            tool = TRANSFER_TOOL
        else:
            legs = await source.segments(points=points, ids=ids)
            tool = ""
    except Exception:
        return {}, ""
    return {(leg.from_id, leg.to_id): leg for leg in legs or []}, tool


def transfer_from(leg: RouteLeg, from_item_id: str, tool: str) -> TransferLeg:
    return TransferLeg(
        from_item_id=from_item_id,
        minutes=int(leg.duration_minutes or 0),
        mode=leg.mode,
        distance_km=leg.distance_km,
        source=leg.source,
        tool=tool if leg.id else "",
        result_id=leg.id,
    )


def transfer_sentence(transfer: TransferLeg | None) -> str:
    if transfer is None:
        return ""
    how = TRANSPORT_NOTE.get(transfer.mode, f"by {transfer.mode}")
    sentence = f"About {transfer.minutes} min {how} from the previous stop (estimate)."
    if transfer.delay_minutes:
        kind = "an assumed" if transfer.delay_source == "assumed" else "the reported"
        sentence += f" Plus {transfer.delay_minutes} min for {kind} rail delay."
    return sentence


def apply_delay(day: DayPlan, delay: int, *, tool: str, source: str) -> int:
    """Record a reported rail delay on the day's metro transfers. Returns how many changed.

    Each metro leg gets ``delay`` extra minutes, unless walking is faster (then
    only up to the walk). ``tool`` names the status tool that stated the delay's
    length, or is empty with ``source="assumed"`` when the feed gave none.
    """
    changed = 0
    ordered = sorted(day.items, key=lambda item: item.start)
    by_id = {item.id: item for item in ordered}
    for item in ordered:
        transfer = item.transfer
        previous = by_id.get(transfer.from_item_id) if transfer else None
        if transfer is None or transfer.mode != "metro" or previous is None or previous.geo is None or item.geo is None:
            continue
        extra = delay_allowance(previous.geo, item.geo, transfer.minutes, delay)
        if extra <= 0:
            continue
        transfer.delay_minutes, transfer.delay_tool, transfer.delay_source = extra, tool, source
        item.notes = with_transfer_note(item.notes, transfer, meal=item.type.value == "meal")
        changed += 1
    return changed


def delay_allowance(origin: GeoPoint, destination: GeoPoint, minutes: int, delay: int) -> int:
    """Extra minutes a metro leg needs for ``delay``: the delay, or less when walking is faster."""
    km = haversine_km(origin.lat, origin.lng, destination.lat, destination.lng)
    walk = round(km / TRANSPORT_SPEED_KMH["walk"] * 60)
    return max(0, min(minutes + delay, walk) - minutes)


def with_transfer_note(notes: str, transfer: TransferLeg | None, *, meal: bool = False) -> str:
    """Replace the item's transfer sentence, so the text never disagrees with the leg.

    A meal's note carries no transfer sentence; its leg is on the item only.
    """
    base = _NOTE.sub("", notes or "").strip()
    if meal:
        return base[:700]
    sentence = transfer_sentence(transfer)
    return f"{base} {sentence}".strip()[:700] if sentence else base[:700]


def _minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _round5(minutes: int) -> int:
    return -(-minutes // 5) * 5


def _signature(items: list[PlanItem]) -> list[tuple]:
    return [
        (item.id, (item.geo.lat, item.geo.lng) if item.geo else None, item.start, item.end)
        for item in sorted(items, key=lambda entry: entry.start)
    ]


async def refresh_transfers(
    old: TripPlan | None,
    plan: TripPlan,
    *,
    toolbox: Any | None,
    source: Any | None,
    buffer: int = 0,
) -> list[str]:
    """After an edit, give every changed day transfers that match its stops.

    Only days whose stops, places or times changed are touched. A transfer whose
    previous stop or coordinates changed is fetched again (one logged call per
    day), and a stop that now starts before the previous stop's end plus the
    transfer is moved later, with the rest of the day after it. A day that would
    then end after 23:30, or would move a locked or confirmed stop, keeps its
    times. Returns the names of stops that were moved.
    """
    old_geo: dict[str, GeoPoint | None] = {}
    old_days: list[list[tuple]] = []
    if old is not None:
        for day in old.days:
            old_days.append(_signature(day.items))
            for item in day.items:
                old_geo[item.id] = item.geo
    moved: list[str] = []
    for index, day in enumerate(plan.days):
        if index < len(old_days) and old_days[index] == _signature(day.items):
            continue
        ordered = sorted(day.items, key=lambda item: item.start)
        stale: list[tuple[PlanItem, PlanItem]] = []
        for position, item in enumerate(ordered):
            previous = ordered[position - 1] if position else None
            if previous is None or previous.geo is None or item.geo is None:
                if item.transfer is not None:
                    item.transfer = None
                    item.notes = with_transfer_note(item.notes, None, meal=item.type.value == "meal")
                continue
            current = item.transfer
            same_ends = (
                previous.id in old_geo
                and item.id in old_geo
                and old_geo[previous.id] == previous.geo
                and old_geo[item.id] == item.geo
            )
            if current is None or current.from_item_id != previous.id or not same_ends:
                stale.append((previous, item))
        if stale:
            legs, tool = await fetch_legs(
                [(item.id, item.geo) for item in ordered if item.geo is not None],
                toolbox=toolbox,
                source=source,
            )
            for previous, item in stale:
                leg = legs.get((previous.id, item.id))
                item.transfer = transfer_from(leg, previous.id, tool) if leg else None
                item.notes = with_transfer_note(item.notes, item.transfer, meal=item.type.value == "meal")
        moved.extend(_push_later(ordered, buffer))
    return moved


def _push_later(ordered: list[PlanItem], buffer: int) -> list[str]:
    times: list[tuple[int, int]] = []
    previous_end: int | None = None
    changed = False
    for item in ordered:
        start, end = _minutes(item.start), _minutes(item.end)
        if previous_end is not None and item.transfer is not None:
            earliest = previous_end + item.transfer.minutes + item.transfer.delay_minutes + buffer
            if start < earliest:
                if item.locked or item.confirmed:
                    return []
                shift = _round5(earliest) - start
                start, end = start + shift, end + shift
                changed = True
        elif previous_end is not None and start < previous_end:
            if item.locked or item.confirmed:
                return []
            shift = previous_end - start
            start, end = start + shift, end + shift
            changed = True
        times.append((start, end))
        previous_end = end
    if not changed or times[-1][1] > LATEST_END:
        return []
    moved: list[str] = []
    for item, (start, end) in zip(ordered, times):
        if (item.start, item.end) != (_hhmm(start), _hhmm(end)):
            item.start, item.end = _hhmm(start), _hhmm(end)
            moved.append(item.name)
    return moved
