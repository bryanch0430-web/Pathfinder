"""Hong Kong disruptions in a new plan: typhoon and rainstorm signals, MTR delays.

Proposal TC-03 (p4 l.144-148). After the planner composes a Hong Kong plan, this
module asks the registry's official status sources through the toolbox, so each
call is allowlisted, logged as a tool span and screened like any tool result:

- ``weather.warnings`` (Hong Kong Observatory). On a day with a severe warning
  (typhoon signal No. 3 or above, red or black rainstorm) each outdoor stop is
  swapped with an indoor stop from a day without one, or replaced by an unused
  indoor candidate, or dropped. A stop the traveller asked for or locked stays,
  and the disruption is left open.
- ``routes.status`` (MTR Next Train). When a line that serves one of a day's stops
  reports a delay, every metro transfer that day gets the delay added (the
  planner's transport model does not route individual lines), and later stops
  move back so each transfer still fits.

Each disruption is recorded on ``plan.disruptions``, on the day's note and in the
plan summary the traveller reads. A source that fails becomes a data gap; the
plan is otherwise returned as the planner made it.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from app.application.planner import (
    PACE_BUFFER_MINUTES,
    TRANSPORT_SPEED_KMH,
    _hhmm,
    _is_must_see,
    _minutes_of,
    _must_see,
    _pace,
    _required_names,
    _round5,
    _schedule_day,
    _travel,
)
from app.application.transfers import apply_delay, refresh_transfers
from app.config import Settings
from app.domain.errors import ProviderError
from app.domain.models import (
    AgentResult,
    DataGap,
    DataStatus,
    DayPlan,
    Destination,
    Disruption,
    FailureKind,
    GeoPoint,
    ItemType,
    PlanItem,
    TransitStatus,
    TripContext,
    TripPlan,
    WeatherAlert,
    WeatherDay,
)
from app.domain.place_names import english_name
from app.infrastructure.http import haversine_km
from app.infrastructure.providers.hong_kong import MTR_LINES

# Place types that keep a visitor indoors (Google place types, as the sources tag them).
INDOOR_TYPES = frozenset(
    {
        "museum",
        "art_gallery",
        "aquarium",
        "shopping_mall",
        "department_store",
        "movie_theater",
        "library",
        "performing_arts_theater",
        "planetarium",
    }
)

# The MTR feed reports a delay flag, not its length: this is the buffer added to
# each metro transfer when the source gives no figure.
DEFAULT_DELAY_MINUTES = 20
# A stop pushed past this end time by a delay is dropped rather than scheduled.
LATEST_END = 23 * 60

# MTR stations: code -> (lat, lng, lines). Used only to tell which lines serve a
# day's stops; stations within WALK_KM of a stop serve it, else the nearest
# within NEAR_KM.
WALK_KM = 1.0
NEAR_KM = 3.0
MTR_STATIONS: dict[str, tuple[float, float, tuple[str, ...]]] = {
    "KET": (22.2812, 114.1289, ("ISL",)),
    "HKU": (22.2841, 114.1351, ("ISL",)),
    "SYP": (22.2856, 114.1426, ("ISL",)),
    "SHW": (22.2866, 114.1520, ("ISL",)),
    "CEN": (22.2820, 114.1582, ("ISL", "TWL")),
    "HOK": (22.2849, 114.1582, ("TCL", "AEL")),
    "ADM": (22.2790, 114.1646, ("ISL", "TWL", "SIL", "EAL")),
    "WAC": (22.2775, 114.1730, ("ISL",)),
    "EXC": (22.2817, 114.1755, ("EAL",)),
    "CAB": (22.2802, 114.1838, ("ISL",)),
    "TIH": (22.2824, 114.1918, ("ISL",)),
    "FOH": (22.2880, 114.1937, ("ISL",)),
    "NOP": (22.2913, 114.2006, ("ISL", "TKL")),
    "QUB": (22.2879, 114.2096, ("ISL", "TKL")),
    "TAK": (22.2846, 114.2161, ("ISL",)),
    "SWH": (22.2817, 114.2223, ("ISL",)),
    "SKW": (22.2790, 114.2290, ("ISL",)),
    "HFC": (22.2768, 114.2400, ("ISL",)),
    "CHW": (22.2646, 114.2371, ("ISL",)),
    "OCP": (22.2486, 114.1742, ("SIL",)),
    "WCH": (22.2480, 114.1680, ("SIL",)),
    "LET": (22.2420, 114.1562, ("SIL",)),
    "SOH": (22.2427, 114.1490, ("SIL",)),
    "TST": (22.2973, 114.1722, ("TWL",)),
    "ETS": (22.2951, 114.1745, ("TML",)),
    "AUS": (22.3043, 114.1666, ("TML",)),
    "KOW": (22.3049, 114.1615, ("TCL", "AEL")),
    "JOR": (22.3049, 114.1717, ("TWL",)),
    "HUH": (22.3029, 114.1817, ("TML", "EAL")),
    "HOM": (22.3093, 114.1827, ("KTL", "TML")),
    "WHA": (22.3050, 114.1895, ("KTL",)),
    "YMT": (22.3129, 114.1706, ("TWL", "KTL")),
    "OLY": (22.3178, 114.1602, ("TCL",)),
    "MOK": (22.3193, 114.1694, ("TWL", "KTL")),
    "MKK": (22.3221, 114.1724, ("EAL",)),
    "PRE": (22.3245, 114.1683, ("TWL", "KTL")),
    "TKW": (22.3171, 114.1878, ("TML",)),
    "SUW": (22.3259, 114.1912, ("TML",)),
    "KAT": (22.3305, 114.1993, ("TML",)),
    "SKM": (22.3318, 114.1688, ("KTL",)),
    "SSP": (22.3307, 114.1622, ("TWL",)),
    "NAC": (22.3267, 114.1537, ("TCL", "TML")),
    "CSW": (22.3354, 114.1563, ("TWL",)),
    "LCK": (22.3373, 114.1480, ("TWL",)),
    "MEF": (22.3381, 114.1376, ("TWL", "TML")),
    "LAK": (22.3483, 114.1261, ("TWL", "TCL")),
    "KWF": (22.3569, 114.1279, ("TWL",)),
    "TSW": (22.3736, 114.1177, ("TWL",)),
    "TWW": (22.3683, 114.1097, ("TML",)),
    "TSY": (22.3583, 114.1078, ("TCL", "AEL")),
    "KOT": (22.3369, 114.1762, ("KTL", "EAL")),
    "LOF": (22.3380, 114.1872, ("KTL",)),
    "WTS": (22.3417, 114.1938, ("KTL",)),
    "DIH": (22.3401, 114.2016, ("KTL", "TML")),
    "CHH": (22.3348, 114.2090, ("KTL",)),
    "KOB": (22.3232, 114.2141, ("KTL",)),
    "NTK": (22.3154, 114.2192, ("KTL",)),
    "KWT": (22.3122, 114.2263, ("KTL",)),
    "LAT": (22.3069, 114.2330, ("KTL",)),
    "YAT": (22.2979, 114.2371, ("KTL", "TKL")),
    "TIK": (22.3043, 114.2526, ("KTL", "TKL")),
    "TKO": (22.3075, 114.2600, ("TKL",)),
    "HAH": (22.3155, 114.2645, ("TKL",)),
    "POA": (22.3225, 114.2578, ("TKL",)),
    "LHP": (22.2954, 114.2689, ("TKL",)),
    "TAW": (22.3726, 114.1787, ("TML", "EAL")),
    "SHT": (22.3826, 114.1872, ("EAL",)),
    "SUN": (22.3317, 114.0289, ("TCL", "DRL")),
    "DIS": (22.3156, 114.0450, ("DRL",)),
    "TUC": (22.2893, 113.9415, ("TCL",)),
    "AIR": (22.3159, 113.9365, ("AEL",)),
}
LINE_NAMES = {code: name for code, (name, _station) in MTR_LINES.items()}


def line_code(name: str) -> str:
    """``"Tsuen Wan Line"`` or ``"TWL"`` -> ``"TWL"``; empty when unknown."""
    text = name.strip().lower()
    for code, label in LINE_NAMES.items():
        if text in {code.lower(), label.lower(), label.lower().removesuffix(" line")}:
            return code
    return ""


def is_hong_kong(destination: Destination) -> bool:
    code = (destination.country_code or "").upper()
    if code == "HK":
        return True
    name = destination.name.lower()
    if "hong kong" in name or "香港" in destination.name:
        return code in {"", "XX", "ZZ"}
    if code or destination.geo is None:
        return False
    geo = destination.geo
    return 22.15 <= geo.lat <= 22.56 and 113.83 <= geo.lng <= 114.45


def lines_serving(points: list[GeoPoint]) -> set[str]:
    """MTR lines with a station a walk from any of ``points``."""
    lines: set[str] = set()
    for point in points:
        distances = sorted(
            (haversine_km(point.lat, point.lng, lat, lng), served)
            for lat, lng, served in MTR_STATIONS.values()
        )
        near = [served for km, served in distances if km <= WALK_KM]
        if not near and distances and distances[0][0] <= NEAR_KM:
            near = [distances[0][1]]
        for served in near:
            lines.update(served)
    return lines


def transfer_minutes(origin: GeoPoint, destination: GeoPoint, delay: int = 0) -> int:
    """The planner's transport model, with ``delay`` added to a metro leg unless walking is faster."""
    km = haversine_km(origin.lat, origin.lng, destination.lat, destination.lng)
    mode, minutes = _travel(km)
    if delay and mode == "metro":
        walk = round(km / TRANSPORT_SPEED_KMH["walk"] * 60)
        minutes = min(minutes + delay, walk)
    return minutes


def metro_transfers(day: DayPlan) -> int:
    ordered = sorted(day.items, key=lambda item: item.start)
    count = 0
    for previous, current in zip(ordered, ordered[1:]):
        if previous.geo and current.geo:
            km = haversine_km(previous.geo.lat, previous.geo.lng, current.geo.lat, current.geo.lng)
            count += _travel(km)[0] == "metro"
    return count


def retime_day(day: DayPlan, *, delay: int = 0) -> tuple[list[str], list[str]]:
    """Move stops later where a transfer is shorter than the transport model needs.

    Returns the ids of moved items and the names of items dropped because they
    would end after 23:00 or run into a locked stop. A locked item never moves.
    """
    moved: list[str] = []
    dropped: list[str] = []
    kept: list[PlanItem] = []
    for item in sorted(day.items, key=lambda row: row.start):
        previous = kept[-1] if kept else None
        if previous is not None and not item.locked:
            need = transfer_minutes(previous.geo, item.geo, delay) if previous.geo and item.geo else 0
            earliest = _round5(_minutes_of(previous.end) + need)
            start = _minutes_of(item.start)
            if start < earliest:
                length = _minutes_of(item.end) - start
                if earliest + length > LATEST_END:
                    dropped.append(item.name)
                    continue
                item.start, item.end = _hhmm(earliest), _hhmm(earliest + length)
                moved.append(item.id)
        # A stop pushed into a locked one comes off the day: the schedule must not overlap.
        while item.locked and kept and not kept[-1].locked and item.start < kept[-1].end:
            dropped.append(kept.pop().name)
        kept.append(item)
    day.items = kept
    remaining = {item.id for item in kept}
    return [item_id for item_id in moved if item_id in remaining], dropped


def _when(day: date) -> str:
    return f"{day:%a} {day.day} {day:%b}"


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _join(*parts: str) -> str:
    return " ".join(part.strip() for part in parts if part and part.strip())


class _Pool:
    """The attraction candidates the agents returned, by plan item id."""

    def __init__(self, results: dict[str, AgentResult]):
        attraction = results.get("attraction")
        rows = list(attraction.payload.get("places") or []) if attraction is not None else []
        self.rows = [row for row in rows if isinstance(row, dict) and row.get("id") and row.get("geo")]
        self.by_item = {f"item_{row['id']}": row for row in self.rows}

    def indoor(self, item: PlanItem) -> bool:
        row = self.by_item.get(item.id)
        tags = set((row or {}).get("tags") or []) | set(item.tags)
        return bool(tags & INDOOR_TYPES)


def _distance(point: GeoPoint | None, centre: GeoPoint | None) -> float:
    if point is None or centre is None:
        return 1e6
    return haversine_km(point.lat, point.lng, centre.lat, centre.lng)


def _centre(items: list[PlanItem]) -> GeoPoint | None:
    points = [item.geo for item in items if item.geo is not None]
    if not points:
        return None
    return GeoPoint(lat=sum(p.lat for p in points) / len(points), lng=sum(p.lng for p in points) / len(points))


def _retitle(day: DayPlan, destination: str) -> None:
    """Keep a generated "<first stop> and nearby stops" title true after a move."""
    if not day.title.endswith(" and nearby stops"):
        return
    first = next((item for item in sorted(day.items, key=lambda row: row.start) if item.type == ItemType.ATTRACTION), None)
    day.title = f"{english_name(first.name, first.names)} and nearby stops" if first else f"Open day in {destination}"


def _move_outdoor_stops(
    plan: TripPlan,
    index: int,
    alert_days: set[int],
    pool: _Pool,
    required: list[str],
    pace: str,
) -> tuple[list[str], list[str], list[str]]:
    """Clear outdoor stops off ``plan.days[index]``. Returns (actions, kept, affected ids)."""
    day = plan.days[index]
    actions: list[str] = []
    kept: list[str] = []
    affected: list[str] = []
    touched = {index}
    outdoor = [
        item
        for item in sorted(day.items, key=lambda row: row.start)
        if item.type == ItemType.ATTRACTION and not pool.indoor(item)
    ]
    for item in outdoor:
        affected.append(item.id)
        if item.locked:
            kept.append(item.name)
            continue
        centre = _centre([row for row in day.items if row.id != item.id and row.type == ItemType.ATTRACTION and pool.indoor(row)]) or _centre(day.items)
        # 1. Swap with an indoor stop on a day without a severe warning.
        partners = [
            (other_index, other)
            for other_index, other_day in enumerate(plan.days)
            if other_index not in alert_days
            for other in other_day.items
            if other.type == ItemType.ATTRACTION and not other.locked and pool.indoor(other)
        ]
        if partners:
            other_index, other = min(
                partners,
                key=lambda pair: _distance(pair[1].geo, centre) + _distance(item.geo, _centre(plan.days[pair[0]].items)),
            )
            other_day = plan.days[other_index]
            item_length = _minutes_of(item.end) - _minutes_of(item.start)
            other_length = _minutes_of(other.end) - _minutes_of(other.start)
            item_start, other_start = _minutes_of(item.start), _minutes_of(other.start)
            item.start, item.end = _hhmm(other_start), _hhmm(other_start + item_length)
            other.start, other.end = _hhmm(item_start), _hhmm(item_start + other_length)
            day.items = [row for row in day.items if row.id != item.id] + [other]
            other_day.items = [row for row in other_day.items if row.id != other.id] + [item]
            touched.add(other_index)
            actions.append(f"moved {item.name} to {_when(other_day.date)} and {other.name} to {_when(day.date)}")
            continue
        # 2. Replace with an indoor candidate the plan does not use yet.
        used_ids = {row.id for _, row in plan.iter_items()}
        used_names = {row.name.strip().lower() for _, row in plan.iter_items()}
        spare = [
            row
            for row in pool.rows
            if f"item_{row['id']}" not in used_ids
            and str(row.get("name", "")).strip().lower() not in used_names
            and set(row.get("tags") or []) & INDOOR_TYPES
            and row.get("source_refs")
        ]
        if spare:
            row = min(spare, key=lambda candidate: _distance(GeoPoint.model_validate(candidate["geo"]), centre))
            built = _schedule_day(day.date, [row], None, None, pace=pace)
            if built:
                new = built[0]
                length = _minutes_of(new.end) - _minutes_of(new.start)
                new.start, new.end = item.start, _hhmm(_minutes_of(item.start) + length)
                day.items = [new if existing.id == item.id else existing for existing in day.items]
                pool.by_item[new.id] = row
                affected.append(new.id)
                actions.append(f"replaced {item.name} with {new.name}")
                continue
        # 3. A stop the traveller named stays; anything else comes off the day.
        if _is_must_see(item.name, required):
            kept.append(item.name)
            continue
        day.items = [existing for existing in day.items if existing.id != item.id]
        actions.append(f"dropped {item.name} (no indoor alternative came back)")
    for touched_index in sorted(touched):
        touched_day = plan.days[touched_index]
        touched_day.items.sort(key=lambda row: row.start)
        retime_day(touched_day)
        _retitle(touched_day, plan.destination.name)
    return actions, kept, affected


def _record_alert_on_weather(day: DayPlan, alerts: list[WeatherAlert]) -> None:
    names = "; ".join(dict.fromkeys(alert.name for alert in alerts))
    text = f"Official warning (Hong Kong Observatory): {names}."
    if day.weather is None:
        day.weather = WeatherDay(
            date=day.date, status=DataStatus.UNAVAILABLE, summary=names, source=alerts[0].source, note=text
        )
    elif not all(alert.name.lower() in f"{day.weather.summary} {day.weather.note}".lower() for alert in alerts):
        day.weather.note = _join(day.weather.note, text)


def _apply_alerts(plan: TripPlan, alerts: list[WeatherAlert], pool: _Pool, context: TripContext) -> list[str]:
    """Act on each warning date inside the trip. Returns sentences for the traveller."""
    by_date: dict[date, list[WeatherAlert]] = {}
    for alert in alerts:
        by_date.setdefault(alert.date, []).append(alert)
    index_of = {day.date: index for index, day in enumerate(plan.days)}
    severe_days = {
        index_of[day] for day, rows in by_date.items() if day in index_of and any(alert.severe for alert in rows)
    }
    required = _must_see(context) + _required_names(context)
    pace = _pace(context)
    sentences: list[str] = []
    for when, rows in sorted(by_date.items()):
        if when not in index_of:
            continue
        index = index_of[when]
        day = plan.days[index]
        names = "; ".join(dict.fromkeys(alert.name for alert in rows))
        if not any(alert.severe for alert in rows):
            day.note = _join(day.note, f"Hong Kong Observatory: {names} in force. Check before going to outdoor stops.")
            continue
        _record_alert_on_weather(day, [alert for alert in rows if alert.severe])
        actions, kept, affected = _move_outdoor_stops(plan, index, severe_days, pool, required, pace)
        if actions and not kept:
            resolution = f"{_sentence('; '.join(actions))}. That day now has indoor stops only."
        elif kept:
            resolution = (
                _join(f"{_sentence('; '.join(actions))}." if actions else "", f"{', '.join(kept)} stayed on that day")
                + " because you asked for it or locked it; check the Observatory before going."
            )
        else:
            resolution = "Every stop that day is already indoors."
        codes = {alert.code for alert in rows if alert.severe}
        kind = (
            "typhoon_signal"
            if any(code.startswith("TC") for code in codes)
            else "rainstorm_warning" if any(code.startswith("WRAIN") for code in codes) else "weather_warning"
        )
        plan.disruptions.append(
            Disruption(
                kind=kind,
                summary=f"{names} in force on {_when(when)} (Hong Kong Observatory).",
                affected_item_ids=affected,
                resolution=resolution,
                resolved=not kept,
            )
        )
        day.note = _join(day.note, f"{names}: indoor stops only today." if not kept else f"{names}: stay close to shelter.")
        sentences.append(f"The Hong Kong Observatory has {names} in force on {_when(when)}. {resolution}")
    return sentences


def _apply_delays(plan: TripPlan, statuses: list[TransitStatus]) -> list[str]:
    """Add a reported MTR delay to that day's metro transfers. Returns sentences for the traveller."""
    by_date: dict[date, list[TransitStatus]] = {}
    for status in statuses:
        if status.delayed:
            by_date.setdefault(status.date, []).append(status)
    index_of = {day.date: index for index, day in enumerate(plan.days)}
    sentences: list[str] = []
    for when, rows in sorted(by_date.items()):
        if when not in index_of:
            continue
        day = plan.days[index_of[when]]
        served = lines_serving([item.geo for item in day.items if item.geo is not None])
        relevant = [row for row in rows if row.line in served]
        names = ", ".join(dict.fromkeys(row.line_name or LINE_NAMES.get(row.line, row.line) for row in rows))
        summary = f"MTR reports a delay on the {names} on {_when(when)}."
        if not relevant:
            plan.disruptions.append(
                Disruption(
                    kind="transit_delay",
                    summary=summary,
                    resolution="No stop that day is near the delayed line, so nothing changed.",
                    resolved=True,
                )
            )
            continue
        stated = [row.delay_minutes for row in relevant if row.delay_minutes]
        delay = max(stated) if stated else DEFAULT_DELAY_MINUTES
        assumed = "" if stated else f" (the MTR feed does not say how long, so {delay} minutes is assumed)"
        legs = metro_transfers(day)
        moved, dropped = retime_day(day, delay=delay)
        # Each metro transfer records the delay it allows, and which logged status
        # result stated it, so grounding can check leg + delay (Proposal TC-09).
        stating = next((row for row in relevant if row.delay_minutes == delay), None)
        apply_delay(
            day,
            delay,
            tool="routes.status" if stating else "",
            source=(stating.source or "routes.status") if stating else "assumed",
        )
        if not legs:
            resolution = "That day's stops are a walk apart, so no transfer needs the MTR."
        else:
            resolution = (
                f"Each metro transfer that day allows {delay} extra minutes{assumed}; "
                f"{len(moved)} stop(s) start later."
            )
            if dropped:
                resolution += f" Dropped {', '.join(dropped)}, which would have ended after {_hhmm(LATEST_END)}."
        plan.disruptions.append(
            Disruption(
                kind="transit_delay",
                summary=summary,
                affected_item_ids=moved,
                resolution=resolution,
                resolved=True,
            )
        )
        day.note = _join(day.note, f"MTR delay on the {names}: metro transfers allow {delay} extra minutes.")
        _retitle(day, plan.destination.name)
        sentences.append(f"{summary} {resolution}")
    return sentences


async def _ask(
    sources: list[Any],
    *,
    tool: str,
    method: str,
    section: str,
    missing: str,
    toolbox: Any,
    settings: Settings,
    plan: TripPlan,
    **kwargs: Any,
) -> list[Any] | None:
    """The first source that answers wins; a failure becomes a data gap.

    A live-status source that cannot speak for the trip's dates (it reports only
    what is in force today) is not called at all.
    """
    for source in sources:
        if not getattr(source, "available", True):
            continue
        covers = getattr(source, "covers", None)
        if callable(covers) and not covers(start=kwargs["start"], end=kwargs["end"]):
            continue
        try:
            return list(
                await asyncio.wait_for(
                    toolbox.call(tool, getattr(source, method), **kwargs),
                    timeout=min(settings.agent_timeout_seconds, 15.0),
                )
                or []
            )
        except asyncio.TimeoutError:
            kind, detail, retryable = FailureKind.TIMEOUT, "source exceeded its deadline", True
        except ProviderError as exc:
            kind, detail, retryable = exc.kind, str(exc), exc.retryable
        except Exception as exc:  # never let a status source break the plan
            kind, detail, retryable = FailureKind.SERVER_ERROR, exc.__class__.__name__, False
        plan.data_quality.gaps.append(
            DataGap(
                section=section,
                kind=kind,
                message=f"{getattr(source, 'name', tool)}: {detail[:160]}. {missing}",
                retryable=retryable,
            )
        )
    return None


async def apply_hong_kong_disruptions(
    plan: TripPlan,
    *,
    registry: Any,
    toolbox: Any,
    settings: Settings,
    results: dict[str, AgentResult],
    context: TripContext,
) -> TripPlan:
    """Check HKO warnings and MTR status for a Hong Kong plan and adjust it."""
    if not settings.enable_hk_disruptions or toolbox is None or not is_hong_kong(plan.destination):
        return plan
    warnings_sources = list(getattr(registry, "warnings", None) or [])
    transit_sources = list(getattr(registry, "transit", None) or [])
    if not warnings_sources and not transit_sources:
        return plan
    start, end = plan.dates.start, plan.dates.end
    sentences: list[str] = []
    used_sources: set[str] = set()
    # Swapped, replaced or dropped stops get transfers that match their new
    # neighbours, from a logged routes.segments call (Proposal TC-07/TC-09).
    transfers = getattr(registry, "transfers", None)
    buffer = PACE_BUFFER_MINUTES.get(_pace(context), 5)
    before = plan.model_copy(deep=True)

    alerts = await _ask(
        warnings_sources,
        tool="weather.warnings",
        method="alerts",
        section="warnings",
        missing="Typhoon and rainstorm signals were not checked.",
        toolbox=toolbox,
        settings=settings,
        plan=plan,
        start=start,
        end=end,
    )
    if alerts:
        used_sources |= {alert.source for alert in alerts if alert.source}
        sentences += _apply_alerts(plan, alerts, _Pool(results), context)
        await refresh_transfers(before, plan, toolbox=toolbox, source=transfers, buffer=buffer)
        before = plan.model_copy(deep=True)

    lines = sorted(lines_serving([item.geo for _, item in plan.iter_items() if item.geo is not None]))
    if lines:
        statuses = await _ask(
            transit_sources,
            tool="routes.status",
            method="status",
            section="transit",
            missing="MTR delays were not checked.",
            toolbox=toolbox,
            settings=settings,
            plan=plan,
            lines=lines,
            start=start,
            end=end,
        )
        if statuses:
            used_sources |= {status.source for status in statuses if status.source}
            sentences += _apply_delays(plan, statuses)
            await refresh_transfers(before, plan, toolbox=toolbox, source=transfers, buffer=buffer)

    if used_sources:
        plan.data_quality.sources_used = sorted(set(plan.data_quality.sources_used) | used_sources)
    if sentences:
        plan.summary = _join(plan.summary, *sentences)
    return plan
