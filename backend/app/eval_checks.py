"""Deterministic checks behind the proposal's planning metrics.

Proposal TC-07 (p4): constraint satisfaction is met / total per plan, including the
free-text constraints a script can decide. TC-08 (p4): error recovery needs the failed
item gone, an alternative fetched and the failure recorded. TC-09 (p4): every factual
item must trace back to a tool result in the run's own tool log. TC-03 (p4): each Hong
Kong disruption has its own expected handling.

Every check reads only the case, the final plan and the run's tool log. None calls a
model, so a scored run is repeatable offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.application.planner import TRANSPORT_SPEED_KMH, _travel
from app.application.toolbox import Toolbox
from app.application.transfers import delay_allowance
from app.config import Settings
from app.domain.models import (
    EvalCase,
    EvalCaseResult,
    EvalMetric,
    FlightCandidate,
    GeoLocation,
    GeoPoint,
    HotelCandidate,
    ItemType,
    NoteItem,
    PlaceCandidate,
    RouteLeg,
    TransitStatus,
    TripPlan,
    WeatherAlert,
    WeatherDay,
)
from app.infrastructure.http import haversine_km
from app.infrastructure.observability import NoopTracer

# ---------------------------------------------------------------------------
# Tool log: what the app's Toolbox called and what each call returned
# ---------------------------------------------------------------------------


class ToolLog:
    """The run's tool log.

    It is handed to the app's Toolbox as its store, so the status and error class
    of every call are the Toolbox's own record. RecordingToolbox adds what each
    successful call returned, which grounding is checked against.
    """

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []
        self.phase = "plan"

    def log_tool_call(self, **row: Any) -> None:
        self.entries.append(
            {
                "phase": self.phase,
                "tool": str(row.get("tool", "")),
                "status": str(row.get("status", "")),
                "error_class": str(row.get("error_class", "") or ""),
                "source": str(row.get("source", "") or ""),
                "facts": [],
            }
        )

    def facts(self, kind: str, *, phase: str | None = None) -> list[dict[str, Any]]:
        return [
            fact
            for entry in self.entries
            if entry["status"] == "ok" and (phase is None or entry["phase"] == phase)
            for fact in entry["facts"]
            if fact["kind"] == kind
        ]

    def summary(self) -> dict[str, Any]:
        calls: dict[str, int] = {}
        for entry in self.entries:
            key = f"{entry['tool']}:{entry['status']}"
            calls[key] = calls.get(key, 0) + 1
        errors = [
            f"{entry['tool']}:{entry['error_class'] or 'error'}@{entry['source'] or 'unknown'}"
            for entry in self.entries
            if entry["status"] != "ok"
        ]
        return {"calls": calls, "errors": errors}


class RecordingToolbox(Toolbox):
    """The app's Toolbox (same allowlist and logging) that also keeps each result."""

    def __init__(self, log: ToolLog, *, path: str = "plan", request_id: str = "eval"):
        super().__init__(path=path, tracer=NoopTracer(), store=log, request_id=request_id)
        self.log = log

    async def call(self, tool: str, fn, /, **kwargs: Any):  # type: ignore[override]
        result = await super().call(tool, fn, **kwargs)
        # Toolbox.call logged the entry synchronously right before returning.
        if self.log.entries:
            self.log.entries[-1]["facts"] = extract_facts(result)
        return result


def _price(money: Any) -> list[Any] | None:
    return [round(float(money.amount), 2), money.currency] if money is not None else None


def extract_facts(result: Any) -> list[dict[str, Any]]:
    rows = result if isinstance(result, list) else [result]
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, PlaceCandidate):
            out.append(
                {
                    "kind": "place",
                    "name": row.name,
                    "urls": [ref.url for ref in row.source_refs],
                    "tags": list(row.tags),
                    "text": f"{row.category} {row.description}",
                    "hours": list(row.opening_hours),
                }
            )
        elif isinstance(row, HotelCandidate):
            out.append(
                {
                    "kind": "hotel",
                    "name": row.name,
                    "urls": [ref.url for ref in row.source_refs],
                    "price": _price(row.nightly_price),
                }
            )
        elif isinstance(row, FlightCandidate):
            out.append(
                {
                    "kind": "flight",
                    "name": " ".join(part for part in [row.airline, row.flight_number] if part),
                    "urls": [ref.url for ref in row.source_refs],
                    "price": _price(row.price),
                }
            )
        elif isinstance(row, WeatherDay):
            out.append(
                {
                    "kind": "weather",
                    "date": row.date.isoformat(),
                    "text": f"{row.summary} {row.note}".strip(),
                    "high": row.high_c,
                    "low": row.low_c,
                }
            )
        elif isinstance(row, NoteItem):
            out.append({"kind": "note", "name": row.title, "urls": [row.url]})
        elif isinstance(row, GeoLocation):
            out.append({"kind": "geo", "name": row.name})
        elif isinstance(row, RouteLeg):
            out.append(
                {
                    "kind": "leg",
                    "id": row.id,
                    "minutes": row.duration_minutes,
                    "mode": row.mode,
                    "source": row.source,
                    "from": [row.from_geo.lat, row.from_geo.lng],
                    "to": [row.to_geo.lat, row.to_geo.lng],
                }
            )
        elif isinstance(row, WeatherAlert):
            # An official warning (HKO), kept apart from forecast rows so grounding
            # still compares each day's forecast with the forecast tool.
            out.append({"kind": "alert", "date": row.date.isoformat(), "text": row.name, "severe": row.severe, "source": row.source})
        elif isinstance(row, TransitStatus):
            out.append(
                {"kind": "transit", "date": row.date.isoformat(), "line": row.line, "delayed": row.delayed,
                 "minutes": row.delay_minutes, "source": row.source}
            )
    return out


# ---------------------------------------------------------------------------
# Constraints (TC-07, TC-02)
# ---------------------------------------------------------------------------

_STOP_WORDS = {"the", "a", "an", "of", "at", "in", "and", "closed", "temporarily", "any"}
DIET_RE = re.compile(r"\b(vegetarian|vegan|halal)\b", re.IGNORECASE)
ALERT_RE = re.compile(
    r"typhoon|signal no\.?\s*\d|tropical cyclone|black rainstorm|red rainstorm", re.IGNORECASE
)
CLOSURE_RE = re.compile(r"\b(closed|closure|shut|suspended|not operating|cancel+ed)\b", re.IGNORECASE)
INDOOR_TYPES = {
    "museum",
    "art_gallery",
    "aquarium",
    "shopping_mall",
    "department_store",
    "movie_theater",
    "library",
    "performing_arts_theater",
}
_EXCLUDE_RE = re.compile(
    r"\b(?:do not|don't|never)\s+(?:include|visit|schedule)\s+(.+)|\b(?:avoid|skip|exclude|without)\s+(.+)",
    re.IGNORECASE,
)
_INCLUDE_RE = re.compile(r"\b(?:must|need to|have to)\s+(?:include|visit|see)\s+(.+)|\binclude\s+(.+)", re.IGNORECASE)

KIND_LABELS = {
    "must_include": "Must-include constraints met",
    "exclude": "Exclusion constraints met",
    "dietary": "Dietary constraints met",
    "weather": "Weather-alert constraints met",
    "travel_times": "Travel-time constraints met",
}


def tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9+]+", text.lower().replace("-", " "))
        if token not in _STOP_WORDS
    }


def names_match(target: str, name: str) -> bool:
    wanted = tokens(target)
    return bool(wanted) and wanted <= tokens(name)


@dataclass
class Constraint:
    text: str
    kind: str
    target: str = ""


def classify(text: str) -> tuple[str, str]:
    """Map a written constraint to the deterministic check that decides it."""
    diet = DIET_RE.search(text)
    if diet:
        return "dietary", diet.group(1).lower()
    lower = text.lower()
    if "outdoor" in lower and re.search(r"typhoon|rain|storm|weather|signal|alert", lower):
        return "weather", "outdoor"
    if re.search(r"\b(travel times?|transfers?|journey times?)\b", lower):
        return "travel_times", ""
    if re.search(r"\bbudget\b", lower):
        return "budget", ""
    excluded = _EXCLUDE_RE.search(text)
    if excluded:
        return "exclude", (excluded.group(1) or excluded.group(2) or "").strip(" .")
    included = _INCLUDE_RE.search(text)
    if included:
        return "must_include", (included.group(1) or included.group(2) or "").strip(" .")
    return "unchecked", ""


def parse_constraints(case: EvalCase) -> list[Constraint]:
    out: list[Constraint] = []
    budget: Constraint | None = None
    if case.budget is not None:
        budget = Constraint(f"Stay within {case.currency} {case.budget:,.0f}", "budget")
        out.append(budget)
    if case.days:
        out.append(Constraint(f"Plan {case.days} day{'s' if case.days != 1 else ''}", "days"))
    if case.party_size:
        out.append(Constraint(f"Plan for {case.party_size} traveller{'s' if case.party_size != 1 else ''}", "party_size"))
    for text in case.constraints:
        kind, target = classify(text)
        if kind == "budget" and budget is not None:
            # "stay within budget" is the same constraint as the budget cap.
            budget.text = f"{text} ({case.currency} {case.budget:,.0f})"
            continue
        out.append(Constraint(text, kind, target))
    return out


def _minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _place_fact(log: ToolLog, name: str) -> dict[str, Any] | None:
    key = " ".join(sorted(tokens(name)))
    for fact in log.facts("place"):
        if " ".join(sorted(tokens(fact["name"]))) == key:
            return fact
    return None


def _diet_ok(diet: str, fact: dict[str, Any] | None) -> bool:
    if fact is None:
        return False
    accepted = {f"{diet}_restaurant"} | ({"vegan_restaurant"} if diet == "vegetarian" else set())
    if accepted & set(fact.get("tags") or []):
        return True
    words = {diet} | ({"vegan"} if diet == "vegetarian" else set())
    text = f"{fact['name']} {fact.get('text', '')}".lower()
    return any(re.search(rf"\b{word}\b", text) for word in words)


def alert_dates(log: ToolLog) -> set[str]:
    """Dates a weather tool (forecast or official warnings) reported a severe-weather alert for."""
    forecast = {fact["date"] for fact in log.facts("weather") if ALERT_RE.search(fact.get("text", ""))}
    warnings = {fact["date"] for fact in log.facts("alert") if fact.get("severe") and ALERT_RE.search(fact.get("text", ""))}
    return forecast | warnings


def _indoor(log: ToolLog, item) -> bool:
    fact = _place_fact(log, item.name)
    tags = set(fact.get("tags") or []) if fact else set(item.tags)
    return bool(tags & INDOOR_TYPES)


def outdoor_on_alert_days(plan: TripPlan, log: ToolLog) -> list[str]:
    dates = alert_dates(log)
    return [
        f"{item.name} (day {index + 1})"
        for index, day in enumerate(plan.days)
        if day.date.isoformat() in dates
        for item in day.items
        if item.type == ItemType.ATTRACTION and not _indoor(log, item)
    ]


def transit_minutes(origin: GeoPoint, destination: GeoPoint, delay: int = 0) -> int:
    """Minutes a transfer needs under the app's published transport model.

    The model is the planner's (the one the console map uses, served to the
    planner as the routes.segments tool): walk under 2.5 km, metro under 35 km,
    then car. A metro delay is added to metro legs, unless walking is then faster.
    Meal transfers are checked against the same model as every other stop.
    """
    km = haversine_km(origin.lat, origin.lng, destination.lat, destination.lng)
    mode, minutes = _travel(km)
    if delay and mode == "metro":
        walk = round(km / TRANSPORT_SPEED_KMH["walk"] * 60)
        minutes = min(minutes + delay, walk)
    return minutes


def short_transfers(plan: TripPlan, *, delay_dates: set[str] | None = None, delay: int = 0, only_delay_days: bool = False) -> list[str]:
    """Consecutive stops whose gap is shorter than the transport model needs."""
    delay_dates = delay_dates or set()
    out: list[str] = []
    for index, day in enumerate(plan.days):
        delayed = day.date.isoformat() in delay_dates
        if only_delay_days and not delayed:
            continue
        ordered = sorted(day.items, key=lambda item: item.start)
        for previous, current in zip(ordered, ordered[1:]):
            if previous.geo is None or current.geo is None:
                continue
            gap = _minutes(current.start) - _minutes(previous.end)
            needed = transit_minutes(previous.geo, current.geo, delay if delayed else 0)
            if gap < needed:
                out.append(f"day {index + 1}: {previous.name} -> {current.name} has {gap} min, needs {needed}")
    return out


def check_constraint(
    constraint: Constraint,
    *,
    case: EvalCase,
    plan: TripPlan,
    log: ToolLog,
    delay_dates: set[str],
    delay: int,
) -> tuple[bool, str]:
    kind = constraint.kind
    if kind == "budget":
        if plan.cost.status == "within":
            return True, f"Known cost {plan.cost.currency} {plan.cost.total:,.0f} is within {case.budget:,.0f}."
        if plan.cost.status == "over":
            return False, f"Known cost {plan.cost.currency} {plan.cost.total:,.0f} is over {case.budget:,.0f}."
        return False, "The plan's cost could not be compared with the budget."
    if kind == "days":
        return len(plan.days) == case.days, f"The plan has {len(plan.days)} days."
    if kind == "party_size":
        return plan.party_size == case.party_size, f"The plan is for {plan.party_size}."
    names = [item.name for _, item in plan.iter_items()]
    if kind == "must_include":
        found = [name for name in names if names_match(constraint.target, name)]
        return bool(found), (f"Planned: {found[0]}." if found else f"No planned stop matches {constraint.target!r}.")
    if kind == "exclude":
        found = [name for name in names if names_match(constraint.target, name)]
        return not found, (f"Still planned: {', '.join(found)}." if found else f"No stop matches {constraint.target!r}.")
    if kind == "dietary":
        meals = [item for _, item in plan.iter_items() if item.type == ItemType.MEAL]
        if not meals:
            return False, "No meal stop is planned, so the diet cannot be shown to hold."
        wrong = [item.name for item in meals if not _diet_ok(constraint.target, _place_fact(log, item.name))]
        if wrong:
            return False, f"Not {constraint.target} per the place data: {', '.join(wrong)}."
        return True, f"All {len(meals)} meal stops are {constraint.target} per the place data."
    if kind == "weather":
        offenders = outdoor_on_alert_days(plan, log)
        if offenders:
            return False, f"Outdoor stops on an alert day: {', '.join(offenders)}."
        if not alert_dates(log):
            return True, "No weather alert was reported for the trip dates."
        return True, "No outdoor stop is scheduled on an alert day."
    if kind == "travel_times":
        short = short_transfers(plan, delay_dates=delay_dates, delay=delay)
        if short:
            return False, f"{len(short)} transfer(s) too short: {short[0]}."
        return True, "Every transfer leaves the time the transport model needs."
    return False, "No deterministic check covers this constraint; it needs a judge."


# ---------------------------------------------------------------------------
# Grounding (TC-09)
# ---------------------------------------------------------------------------


def _refs_match(refs, fact: dict[str, Any]) -> bool:
    urls = set(fact.get("urls") or [])
    return bool(refs) and all(ref.url and ref.url in urls for ref in refs)


def _same_point(point: GeoPoint | None, pair: Any) -> bool:
    if point is None or not isinstance(pair, (list, tuple)) or len(pair) != 2:
        return False
    return abs(point.lat - float(pair[0])) < 1e-6 and abs(point.lng - float(pair[1])) < 1e-6


def _leg_label(index: int, previous, item) -> str:
    return f"transfer: day {index + 1} {previous.name} -> {item.name}"


def _delay_grounded(previous, item, day: str, delays: dict[str, list[dict[str, Any]]]) -> bool:
    """A delay on a transfer needs a logged delayed-line report for that day.

    A stated length must be the logged one (or less, when walking is faster); an
    assumed length is accepted only as labelled ("assumed") and only on a day a
    status tool reported a delay.
    """
    transfer = item.transfer
    reports = delays.get(day, [])
    if not reports:
        return False
    if not transfer.delay_tool:
        return transfer.delay_source == "assumed"
    return any(
        fact["tool"] == transfer.delay_tool
        and fact.get("minutes")
        and transfer.delay_minutes == delay_allowance(previous.geo, item.geo, transfer.minutes, int(fact["minutes"]))
        for fact in reports
    )


def _transfer_grounded(
    previous, item, legs: dict[str, list[dict[str, Any]]], day: str = "", delays: dict[str, list[dict[str, Any]]] | None = None
) -> bool:
    """The item's transfer is the logged leg from the actual previous stop, plus any logged delay."""
    transfer = item.transfer
    if transfer is None or transfer.from_item_id != previous.id or not transfer.tool or not transfer.result_id:
        return False
    if transfer.delay_minutes and not _delay_grounded(previous, item, day, delays or {}):
        return False
    for fact in legs.get(transfer.result_id, []):
        if (
            fact.get("tool") == transfer.tool
            and fact.get("minutes") == transfer.minutes
            and fact.get("mode") == transfer.mode
            and fact.get("source") == transfer.source
            and _same_point(previous.geo, fact.get("from"))
            and _same_point(item.geo, fact.get("to"))
        ):
            return True
    return False


def _hours_grounded(item, places: list[dict[str, Any]]) -> bool:
    """Every stated hours line appears in a place result for this stop (by name or source URL)."""
    if item.hours_status != "stated" or not item.opening_hours:
        return False
    urls = {ref.url for ref in item.source_refs if ref.url}
    key = item.name.strip().lower()
    for fact in places:
        if fact["name"].strip().lower() != key and not urls & set(fact.get("urls") or []):
            continue
        if set(item.opening_hours) <= set(fact.get("hours") or []):
            return True
    return False


def grounding(plan: TripPlan, log: ToolLog) -> dict[str, Any]:
    """Cross-check each fact in the plan against the run's tool results (Proposal TC-09).

    Checked: each stop's name and sources, each hotel's name and nightly price,
    each fare, each day's forecast, each transfer time between consecutive stops
    (the leg must be the logged routes result for exactly those two places), and
    each stop's stated opening hours. A stop whose source gave no hours carries
    "hours unknown" and no hours fact; it is counted, not checked.
    """
    places = log.facts("place")
    hotels = log.facts("hotel")
    flights = log.facts("flight")
    weather = {fact["date"]: fact for fact in log.facts("weather")}
    legs: dict[str, list[dict[str, Any]]] = {}
    delays: dict[str, list[dict[str, Any]]] = {}
    for entry in log.entries:
        if entry["status"] != "ok":
            continue
        for fact in entry["facts"]:
            if fact["kind"] == "leg" and fact.get("id"):
                legs.setdefault(fact["id"], []).append({**fact, "tool": entry["tool"]})
            elif fact["kind"] == "transit" and fact.get("delayed"):
                delays.setdefault(fact["date"], []).append({**fact, "tool": entry["tool"]})
    kinds = ("place", "hotel", "ticket", "weather", "transfer", "opening_hours")
    checked = {kind: 0 for kind in kinds}
    missed: dict[str, list[str]] = {kind: [] for kind in kinds}
    hours_unknown: list[str] = []

    def record(kind: str, ok: bool, label: str) -> None:
        checked[kind] += 1
        if not ok:
            missed[kind].append(label)

    def by_name(facts: list[dict[str, Any]], name: str) -> list[dict[str, Any]]:
        return [fact for fact in facts if fact["name"].strip().lower() == name.strip().lower()]

    for _, item in plan.iter_items():
        matches = by_name(places, item.name)
        ok = item.data_status.value != "unavailable" and any(_refs_match(item.source_refs, fact) for fact in matches)
        record("place", ok, f"{item.type.value}: {item.name}")
        if item.opening_hours or item.hours_status == "stated":
            record("opening_hours", _hours_grounded(item, places), f"hours: {item.name}")
        else:
            hours_unknown.append(item.name)
    for index, day in enumerate(plan.days):
        ordered = sorted(day.items, key=lambda entry: entry.start)
        for position, item in enumerate(ordered):
            previous = ordered[position - 1] if position else None
            if previous is not None and previous.geo is not None and item.geo is not None:
                ok = _transfer_grounded(previous, item, legs, day.date.isoformat(), delays)
                record("transfer", ok, _leg_label(index, previous, item))
            elif item.transfer is not None:
                # A transfer with no previous stop to come from is not a tool fact.
                record("transfer", False, f"transfer: day {index + 1} -> {item.name}")
    for hotel in plan.hotels:
        ok = any(
            _refs_match(hotel.source_refs, fact) and fact.get("price") == _price(hotel.price_per_night)
            for fact in by_name(hotels, hotel.name)
        )
        record("hotel", ok, f"hotel: {hotel.name}")
    for ticket in plan.tickets:
        ok = any(
            _refs_match(ticket.source_refs, fact) and fact.get("price") == _price(ticket.price)
            for fact in by_name(flights, ticket.name)
        )
        record("ticket", ok, f"ticket: {ticket.name}")
    for day in plan.days:
        if day.weather is None or not day.weather.is_available:
            continue
        fact = weather.get(day.date.isoformat())
        ok = bool(fact) and fact.get("high") == day.weather.high_c and fact.get("low") == day.weather.low_c
        record("weather", ok, f"weather: {day.date.isoformat()}")
    total = sum(checked.values())
    ungrounded = [label for kind in kinds for label in missed[kind]]
    grounded = total - len(ungrounded)
    return {
        "items": total,
        "grounded": grounded,
        "ungrounded": ungrounded[:12],
        "score": round(grounded / total, 3) if total else 0.0,
        "by_kind": {
            kind: {"checked": checked[kind], "grounded": checked[kind] - len(missed[kind]), "ungrounded": missed[kind][:6]}
            for kind in kinds
        },
        "hours_unknown": len(hours_unknown),
        "not_checked": (
            f"{len(hours_unknown)} stop(s) carry no opening hours because their source gave none."
            if hours_unknown
            else ""
        ),
    }


# ---------------------------------------------------------------------------
# Disruptions and recovery (TC-03, TC-08)
# ---------------------------------------------------------------------------

DISRUPTION_LABELS = {
    "typhoon": "Typhoon signal handled",
    "venue_closure": "Venue closure recovered",
    "transit_delay": "MTR delay handled",
    "source_timeout": "Source timeout recovered",
}


def fault_dates(case: EvalCase, start: date) -> set[str]:
    day = int(case.fault_detail.get("day", 1) or 1)
    return {(start + timedelta(days=day - 1)).isoformat()}


def _plan_mentions(plan: TripPlan, pattern: re.Pattern[str], *also: str) -> bool:
    texts = [gap.message for gap in plan.data_quality.gaps] + list(plan.data_quality.notes)
    texts += [f"{entry.kind} {entry.summary} {entry.resolution}" for entry in plan.disruptions]
    texts += [day.note for day in plan.days]
    for text in texts:
        if pattern.search(text) and all(word.lower() in text.lower() for word in also):
            return True
    return False


def disruption_outcomes(
    case: EvalCase,
    *,
    initial: TripPlan,
    plan: TripPlan,
    log: ToolLog,
    start: date,
    other_constraints_hold: bool,
) -> dict[str, Any]:
    fault = case.fault
    outcomes: dict[str, bool] = {}
    evidence: dict[str, str] = {}
    if fault == "typhoon":
        dates = alert_dates(log)
        outcomes["alert_detected"] = bool(dates & fault_dates(case, start))
        evidence["alert_detected"] = f"Weather tool alert dates: {sorted(dates) or 'none'}."
        recorded = [
            day.date.isoformat()
            for day in plan.days
            if day.date.isoformat() in dates and day.weather and ALERT_RE.search(f"{day.weather.summary} {day.weather.note}")
        ]
        outcomes["alert_recorded"] = bool(dates) and len(recorded) == len(dates & {d.date.isoformat() for d in plan.days})
        evidence["alert_recorded"] = f"Plan days carrying the alert: {recorded or 'none'}."
        offenders = outdoor_on_alert_days(plan, log)
        outcomes["outdoor_stops_moved"] = bool(dates) and not offenders
        evidence["outdoor_stops_moved"] = (
            f"Outdoor stops left on the alert day: {', '.join(offenders)}." if offenders else "No outdoor stop on the alert day."
        )
    elif fault == "venue_closure":
        venue = str(case.fault_detail.get("venue", ""))
        before = [item for _, item in initial.iter_items() if names_match(venue, item.name)]
        outcomes["venue_in_initial_plan"] = bool(before)
        evidence["venue_in_initial_plan"] = f"{venue} was {'in' if before else 'not in'} the plan before the closure."
        still = [item.name for _, item in plan.iter_items() if names_match(venue, item.name)]
        outcomes["invalid_item_removed"] = bool(before) and not still
        evidence["invalid_item_removed"] = f"Still planned: {', '.join(still)}." if still else f"{venue} is gone from the plan."
        fetched = {fact["name"] for fact in log.facts("place", phase="disruption")}
        replacements = []
        for old in before:
            found = plan.find_item(old.id)
            if found and found[1].name != old.name:
                replacements.append(found[1].name)
        outcomes["alternative_fetched"] = bool(replacements) and all(name in fetched for name in replacements)
        evidence["alternative_fetched"] = (
            f"Replaced with {', '.join(replacements)}; fetched after the closure: {len(fetched)} candidates."
            if replacements
            else "The closed venue's slot holds no new stop."
        )
        outcomes["failure_recorded"] = any(
            names_match(venue, f"{entry.summary} {entry.resolution}") for entry in plan.disruptions
        ) or _plan_mentions(plan, CLOSURE_RE, venue)
        evidence["failure_recorded"] = (
            "; ".join(f"{entry.kind}: {entry.summary}" for entry in plan.disruptions) or "No disruption or note names the closure."
        )
    elif fault == "transit_delay":
        delay = int(case.fault_detail.get("delay_minutes", 0) or 0)
        routed = [entry for entry in log.entries if entry["tool"].startswith("routes.")]
        delayed = [
            fact
            for entry in routed
            for fact in entry["facts"]
            if "delay" in str(fact.get("source", "")) or (fact["kind"] == "transit" and fact.get("delayed"))
        ]
        outcomes["delay_detected"] = bool(delayed)
        evidence["delay_detected"] = f"{len(routed)} routes call(s) during planning; {len(delayed)} reported the delay."
        short = short_transfers(plan, delay_dates=fault_dates(case, start), delay=delay, only_delay_days=True)
        outcomes["transfers_absorb_delay"] = not short
        evidence["transfers_absorb_delay"] = (
            f"{len(short)} transfer(s) on the delay day ignore the {delay}-minute delay: {short[0]}." if short else "Delay-day transfers allow for the delay."
        )
        outcomes["delay_recorded"] = _plan_mentions(plan, re.compile(r"\b(delay|delayed|mtr)\b", re.IGNORECASE))
        evidence["delay_recorded"] = "The plan mentions the delay." if outcomes["delay_recorded"] else "Nothing in the plan mentions the delay."
    elif fault == "source_timeout":
        errors = [index for index, entry in enumerate(log.entries) if entry["status"] != "ok" and entry["tool"] == "places.search"]
        outcomes["failure_detected"] = bool(errors)
        evidence["failure_detected"] = "; ".join(log.summary()["errors"]) or "No failed call in the tool log."
        attractions = {item.name for _, item in plan.iter_items() if item.type == ItemType.ATTRACTION}
        later = {
            fact["name"]
            for entry in log.entries[(errors[0] + 1) if errors else 0 :]
            if entry["tool"] == "places.search" and entry["status"] == "ok"
            for fact in entry["facts"]
        }
        outcomes["alternative_fetched"] = bool(errors) and bool(attractions) and attractions <= later
        evidence["alternative_fetched"] = f"{len(attractions & later)} of {len(attractions)} planned stops came from the fallback call."
        outcomes["failure_recorded"] = _plan_mentions(
            plan, re.compile(r"\b(timed out|timeout|failed|fallback|did not answer)\b", re.IGNORECASE)
        )
        evidence["failure_recorded"] = (
            "The plan's data-quality record names the failed source."
            if outcomes["failure_recorded"]
            else "Neither a data gap nor a data note records the failed source."
        )
    outcomes["constraints_still_hold"] = other_constraints_hold
    return {
        "kind": fault,
        "detail": dict(case.fault_detail),
        "outcomes": outcomes,
        "evidence": evidence,
        "handled": all(outcomes.values()),
    }


# ---------------------------------------------------------------------------
# One planning case
# ---------------------------------------------------------------------------


@dataclass
class PlanScore:
    constraints: list[dict[str, Any]] = field(default_factory=list)
    disruption: dict[str, Any] | None = None
    grounding: dict[str, Any] = field(default_factory=dict)
    tool_log: dict[str, Any] = field(default_factory=dict)

    @property
    def met(self) -> int:
        return sum(1 for row in self.constraints if row["met"])

    @property
    def total(self) -> int:
        return len(self.constraints)

    @property
    def satisfaction(self) -> float:
        return round(self.met / self.total, 3) if self.total else 1.0

    def failure_class(self) -> str:
        unmet = [row for row in self.constraints if not row["met"]]
        if unmet:
            return f"unmet_{unmet[0]['kind']}"
        if self.disruption and not self.disruption["handled"]:
            return f"{self.disruption['kind']}_not_handled"
        if self.grounding.get("ungrounded"):
            return "ungrounded_items"
        return ""

    def summary(self) -> str:
        parts = [f"Constraints {self.met}/{self.total} met"]
        unmet = [row for row in self.constraints if not row["met"]]
        if unmet:
            parts[0] += "; unmet: " + "; ".join(f"{row['text']} ({row['evidence']})" for row in unmet[:3])
        if self.disruption:
            flags = ", ".join(f"{key} {'yes' if value else 'NO'}" for key, value in self.disruption["outcomes"].items())
            parts.append(f"{self.disruption['kind']}: {flags}")
        if self.grounding:
            parts.append(f"grounding {self.grounding['grounded']}/{self.grounding['items']} against the tool log")
        return ". ".join(parts) + "."

    def as_dict(self) -> dict[str, Any]:
        return {
            "constraints": self.constraints,
            "constraints_met": self.met,
            "constraints_total": self.total,
            "disruption": self.disruption,
            "recovery": (
                {"handled": self.disruption["handled"], **self.disruption["outcomes"]} if self.disruption else None
            ),
            "grounding": self.grounding,
            "tool_log": self.tool_log,
        }


def score_plan(
    case: EvalCase,
    *,
    plan: TripPlan,
    initial: TripPlan | None,
    log: ToolLog,
    start: date,
) -> PlanScore:
    delay = int(case.fault_detail.get("delay_minutes", 0) or 0) if case.fault == "transit_delay" else 0
    delay_dates = fault_dates(case, start) if delay else set()
    score = PlanScore()
    for constraint in parse_constraints(case):
        met, evidence = check_constraint(
            constraint, case=case, plan=plan, log=log, delay_dates=delay_dates, delay=delay
        )
        score.constraints.append({"text": constraint.text, "kind": constraint.kind, "met": met, "evidence": evidence})
    if case.fault:
        # A constraint the disruption itself is about is scored as an outcome too;
        # "still hold" covers the rest.
        disruption_kinds = {"typhoon": "weather", "venue_closure": "exclude", "transit_delay": "travel_times"}
        skip = disruption_kinds.get(case.fault)
        others = all(row["met"] for row in score.constraints if row["kind"] != skip)
        score.disruption = disruption_outcomes(
            case, initial=initial or plan, plan=plan, log=log, start=start, other_constraints_hold=others
        )
    score.grounding = grounding(plan, log)
    score.tool_log = log.summary()
    return score


# ---------------------------------------------------------------------------
# Run-level metrics and the judge's independence
# ---------------------------------------------------------------------------


def planning_metrics(results: list[EvalCaseResult]) -> list[EvalMetric]:
    metrics: list[EvalMetric] = []

    def mean(key: str) -> list[float]:
        return [result.scores[key] for result in results if key in result.scores]

    values = mean("constraint_satisfaction")
    if values:
        metrics.append(
            EvalMetric(
                key="constraint_satisfaction",
                label="Constraint satisfaction (met/total)",
                value=round(100.0 * sum(values) / len(values), 1),
            )
        )
    rows = [row for result in results for row in (result.plan_checks.get("constraints") or [])]
    if rows:
        metrics.append(
            EvalMetric(
                key="constraints_met",
                label=f"Constraints met (of {len(rows)})",
                value=float(sum(1 for row in rows if row["met"])),
                unit="",
            )
        )
    for kind, label in KIND_LABELS.items():
        of_kind = [row for row in rows if row["kind"] == kind]
        if of_kind:
            metrics.append(
                EvalMetric(
                    key=f"constraint_{kind}",
                    label=label,
                    value=round(100.0 * sum(1 for row in of_kind if row["met"]) / len(of_kind), 1),
                )
            )
    values = mean("error_recovery")
    if values:
        metrics.append(
            EvalMetric(key="error_recovery", label="Error recovery", value=round(100.0 * sum(values) / len(values), 1))
        )
    disruptions = [result.plan_checks.get("disruption") for result in results if result.plan_checks.get("disruption")]
    for fault, label in DISRUPTION_LABELS.items():
        of_fault = [row for row in disruptions if row["kind"] == fault]
        if of_fault:
            metrics.append(
                EvalMetric(
                    key=f"recovery_{fault}",
                    label=label,
                    value=round(100.0 * sum(1 for row in of_fault if row["handled"]) / len(of_fault), 1),
                )
            )
    for key, label in (("grounding", "Grounding (tool log)"), ("edit_efficiency", "Edit preservation")):
        values = mean(key)
        if values:
            metrics.append(EvalMetric(key=key, label=label, value=round(100.0 * sum(values) / len(values), 1)))
    # Proposal TC-09 (p4): transit times and opening hours, each against the tool log.
    grounds = [result.plan_checks.get("grounding") or {} for result in results]
    for kind, key, label in (
        ("transfer", "grounding_transfers", "Transfer times grounded"),
        ("opening_hours", "grounding_hours", "Opening hours grounded"),
    ):
        rows = [ground.get("by_kind", {}).get(kind) for ground in grounds]
        checked = sum(row["checked"] for row in rows if row)
        if checked:
            grounded = sum(row["grounded"] for row in rows if row)
            metrics.append(
                EvalMetric(key=key, label=f"{label} ({grounded}/{checked})", value=round(100.0 * grounded / checked, 1))
            )
    stops = sum(ground.get("by_kind", {}).get("place", {}).get("checked", 0) for ground in grounds)
    if stops:
        unknown = sum(int(ground.get("hours_unknown", 0)) for ground in grounds)
        metrics.append(
            EvalMetric(key="hours_unknown", label=f"Stops with hours unknown (of {stops})", value=float(unknown), unit="")
        )
    return metrics


_FAMILY_PREFIXES = (
    ("deepseek", ("deepseek",)),
    ("xai", ("grok",)),
    ("openai", ("gpt", "chatgpt", "o1", "o3", "o4")),
    ("anthropic", ("claude",)),
    ("google", ("gemini", "gemma")),
    ("meta", ("llama",)),
    ("mistral", ("mistral", "mixtral", "codestral")),
    ("qwen", ("qwen", "qwq")),
)
_FAMILY_HOSTS = (
    ("deepseek", "deepseek"),
    ("xai", "x.ai"),
    ("openai", "openai"),
    ("anthropic", "anthropic"),
    ("google", "googleapis"),
    ("mistral", "mistral"),
)


def model_family(model: str, base_url: str = "") -> str:
    name = (model or "").strip().lower().rsplit("/", 1)[-1]
    for family, prefixes in _FAMILY_PREFIXES:
        if any(name.startswith(prefix) for prefix in prefixes):
            return family
    host = (base_url or "").lower()
    for family, marker in _FAMILY_HOSTS:
        if marker in host:
            return family
    return "unknown"


def judge_family_check(settings: Settings, *, judge_enabled: bool) -> dict[str, Any]:
    """Proposal TC-07 (p6): the judge must come from a different family than the writer."""
    judge_family = model_family(settings.judge_model, settings.judge_base_url)
    writer_family = model_family(settings.writer_model, settings.writer_base_url)
    same = judge_family == writer_family and judge_family != "unknown"
    if same and judge_enabled:
        warning = (
            f"The judge ({settings.judge_model}) is the same model family ({judge_family}) as the "
            f"writer ({settings.writer_model}); the proposal requires a different family."
        )
    elif same:
        warning = (
            f"The judge is off. Enabled with these settings it would share the writer's "
            f"model family ({judge_family})."
        )
    else:
        warning = ""
    return {
        "judge_enabled": judge_enabled,
        "judge_model": settings.judge_model,
        "judge_family": judge_family,
        "writer_model": settings.writer_model,
        "writer_family": writer_family,
        "same_family": same,
        "warning": warning,
    }


def judge_metric(check: dict[str, Any]) -> EvalMetric:
    return EvalMetric(
        key="judge_independence",
        label="Judge from another model family",
        value=0.0 if check["same_family"] else 100.0,
    )


def render_markdown(cases: list[EvalCaseResult]) -> list[str]:
    """Extra report lines for planning runs (constraints, disruption, grounding)."""
    rows = [case for case in cases if case.plan_checks]
    if not rows:
        return []
    lines = [
        "",
        "## Plan checks",
        "",
        "| Case | Constraints | Disruption | Grounding | Transfers | Opening hours |",
        "|---|---:|---|---:|---:|---:|",
    ]
    for case in rows:
        checks = case.plan_checks
        disruption = checks.get("disruption") or {}
        outcome = (
            f"{disruption['kind']}: {'handled' if disruption['handled'] else 'not handled'}" if disruption else "—"
        )
        ground = checks.get("grounding") or {}
        kinds = ground.get("by_kind") or {}

        def share(kind: str) -> str:
            row = kinds.get(kind) or {}
            return f"{row.get('grounded', 0)}/{row.get('checked', 0)}" if row else "—"

        hours = share("opening_hours")
        if ground.get("hours_unknown"):
            hours += f" ({ground['hours_unknown']} unknown)"
        lines.append(
            f"| {case.case_id} | {checks.get('constraints_met', 0)}/{checks.get('constraints_total', 0)} | "
            f"{outcome} | {ground.get('grounded', 0)}/{ground.get('items', 0)} | {share('transfer')} | {hours} |"
        )
    return lines
