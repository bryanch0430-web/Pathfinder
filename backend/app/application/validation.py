"""Plan validation and bounded repair.

Validation is deliberately boring and explicit. Repair re-shapes what is already
present: it may change times, the dates of a stay or a ticket, and wording, and
nothing else. It cannot add, drop or rename a stop, hotel or ticket, or bring a URL
that was not in the original document, and every price, coordinate, source,
forecast and data gap of a repaired plan is the original's. Two faults in the original that this
leaves unfixable are settled afterwards by code: an id shared by two rows, and a forecast filed
under another day's date.
"""

from __future__ import annotations

import copy
import json
import re
from collections import Counter
from datetime import date, time
from typing import Any, TypeVar

from app.application.llm_gateway import LLMGateway
from app.config import Settings
from app.domain.errors import CanaryLeak, PlanInvalidError
from app.domain.models import (
    AgentName,
    AuditReport,
    DataGap,
    DataStatus,
    DayPlan,
    FailureKind,
    HotelStay,
    Money,
    PlanItem,
    TicketOption,
    TripPlan,
)
from app.security import strip_fence_tags


def validate_plan(plan: TripPlan, settings: Settings) -> list[str]:
    errors: list[str] = []
    if len(plan.days) != len(plan.dates.dates):
        errors.append("day count does not match the trip date range")
    if [day.date for day in plan.days] != plan.dates.dates:
        errors.append("days are not continuous or ordered")
    if plan.party_size < 1:
        errors.append("party size must be at least one")
    if len(plan.days) > settings.max_days:
        errors.append(f"trip exceeds the {settings.max_days}-day limit")
    ids: set[str] = set()
    for day in plan.days:
        for item in day.items:
            if item.id in ids:
                errors.append(f"duplicate item id {item.id}")
            ids.add(item.id)
            if not _valid_time(item.start) or not _valid_time(item.end):
                errors.append(f"{item.name}: invalid time")
            if item.end <= item.start:
                errors.append(f"{item.name}: ends before it starts")
            if item.type.value == "attraction" and not item.source_refs:
                errors.append(f"{item.name}: attraction without a source")
            if item.type.value == "attraction" and not item.geo:
                errors.append(f"{item.name}: attraction without coordinates")
        # A day whose stops overlap cannot be read back from the store (DayPlan refuses it).
        ordered = sorted(day.items, key=lambda item: item.start)
        for previous, current in zip(ordered, ordered[1:]):
            if current.start < previous.end:
                errors.append(f"{day.date}: {previous.name} and {current.name} overlap")
        if day.weather and day.weather.date != day.date:
            errors.append(f"{day.date}: weather attached to the wrong date")
    for hotel in plan.hotels:
        if not (plan.dates.start <= hotel.check_in < hotel.check_out <= plan.dates.end):
            errors.append("hotel stay is outside the trip dates")
    for ticket in plan.tickets:
        if ticket.depart_at and ticket.arrive_at and ticket.arrive_at < ticket.depart_at:
            errors.append(f"{ticket.name}: arrival before departure")
        if ticket.depart_at and not (plan.dates.start <= ticket.depart_at.date() <= plan.dates.end):
            errors.append(f"{ticket.name}: departure is outside the trip dates")
    if plan.cost.total < 0:
        errors.append("negative cost total")
    if len(plan.segments) > 1:
        errors.extend(_route_errors(plan))
    return errors


def _route_errors(plan: TripPlan) -> list[str]:
    """The checks of a plan with several cities: a stay for every night, a chosen option on every leg that has any, and
    a travel day that does not start a stop before the leg that brings the traveller there ends."""
    errors: list[str] = []
    for night in plan.dates.dates[:-1]:
        if not any(stay.check_in <= night < stay.check_out for stay in plan.hotels):
            errors.append(f"no stay for the night of {night} in {plan.segment_of(night).destination.name}")
    between = [leg for leg in plan.legs if leg.kind == "between"]
    for before, after in zip(plan.segments, plan.segments[1:]):
        if not any(leg.from_place == before.destination.name and leg.to_place == after.destination.name for leg in between):
            errors.append(f"no transport leg from {before.destination.name} to {after.destination.name}")
    for leg in plan.legs:
        options = plan.options_for(leg.id)
        chosen = sum(1 for option in options if option.selected)
        if options and chosen != 1:
            errors.append(f"transport from {leg.from_place} to {leg.to_place} has {chosen} chosen options")
    for day in plan.days:
        for item in day.items:
            if item.id.startswith("transit_") and item.type.value == "transit":
                for other in day.items:
                    if other is not item and other.start < item.end and other.end > item.start:
                        errors.append(f"{day.date}: {other.name} starts before {item.name} ends")
    return errors


def _valid_time(value: str) -> bool:
    try:
        time.fromisoformat(value)
        return True
    except ValueError:
        return False


async def repair_plan(
    plan: TripPlan,
    errors: list[str],
    gateway: LLMGateway,
    settings: Settings,
    audit: AuditReport | None = None,
) -> TripPlan:
    if not gateway.available:
        raise PlanInvalidError(
            "The plan failed validation and no writer model is available to repair it.",
            code="plan_invalid",
        )
    original = plan
    last_errors = errors
    # What the traveller may be told if the repair fails: our own rule messages. The writer's and
    # the validator's text (it quotes the model's output) only goes back to the writer.
    shown = errors
    notes = _audit_notes(plan, audit)
    for _ in range(settings.max_repairs):
        user = (
            "Validation error:\n"
            + "\n".join(f"- {error}" for error in last_errors)
            + notes
            + "\n\nInvalid plan JSON:\n"
            + plan.model_dump_json()
            + "\n\nReturn the complete corrected plan JSON. Do not add any new place, source URL, price or time."
        )
        try:
            data = await gateway.json("repair", user=user, purpose="plan.repair")
            candidate = TripPlan.model_validate(data)
        except CanaryLeak:
            raise
        except Exception as exc:
            last_errors = [f"repair output was not a valid TripPlan: {exc}"]
            shown = ["repair output was not a valid TripPlan"]
            continue
        candidate.trip_id = plan.trip_id
        candidate.session_id = plan.session_id
        candidate.version = plan.version
        candidate.created_at = plan.created_at
        candidate.updated_at = plan.updated_at
        refusal = _refusal(original, candidate)
        if refusal:
            last_errors = shown = [refusal]
            continue
        _restore_facts(original, candidate)
        remaining = validate_plan(candidate, settings)
        if not remaining:
            return candidate
        last_errors = shown = remaining
        plan = candidate
    raise PlanInvalidError(
        "The plan still fails validation after bounded repair: " + "; ".join(shown[:3]),
    )


#: The most data-check notes a repair is shown.
_MAX_AUDIT_NOTES = 10

#: What a plan row's id adds to the candidate id the audit names (planner.py `_item_id`, `_build_hotel`,
#: `_build_tickets`). A weather finding names a date, which is the date of a day of the plan.
_PLAN_ID_PREFIX = {AgentName.ATTRACTION: "item_", AgentName.HOTEL: "hotel_", AgentName.TICKET: "flight_"}


def _audit_notes(plan: TripPlan, audit: AuditReport | None) -> str:
    """The audit's findings about rows of this plan, as a block of context lines for the writer, or "".

    Only the finding's own text goes in: never an evidence URL, so the notes give the writer nothing
    it could copy into the plan (`_refusal` would refuse it anyway). `detail` comes from tool data and
    model output, so it loses any fence tag and its line breaks.
    """
    if audit is None or not audit.findings:
        return ""
    present = {
        AgentName.ATTRACTION: {item.id for day in plan.days for item in day.items},
        AgentName.HOTEL: {hotel.id for hotel in plan.hotels},
        AgentName.TICKET: {ticket.id for ticket in plan.tickets},
        AgentName.WEATHER: {day.date.isoformat() for day in plan.days},
    }
    lines: list[str] = []
    for finding in audit.findings:
        if finding.item_id is None:
            continue
        plan_id = _PLAN_ID_PREFIX.get(finding.agent, "") + finding.item_id
        if plan_id not in present[finding.agent]:
            continue
        detail = " ".join(strip_fence_tags(finding.detail).split())
        lines.append(f"- {finding.item_id}: {finding.severity.value} {finding.issue.value}: {detail}")
        if len(lines) == _MAX_AUDIT_NOTES:
            break
    if not lines:
        return ""
    return "\n\nData-check notes (context only; you may reword or retime, never add):\n" + "\n".join(lines)


def _names(plan: TripPlan) -> Counter[tuple[str, str]]:
    """Every stop, hotel and ticket of a plan, counted by kind and name (a plan may repeat a name)."""
    names = Counter(("stop", item.name) for day in plan.days for item in day.items)
    names.update(("hotel", hotel.name) for hotel in plan.hotels)
    names.update(("ticket", ticket.name) for ticket in plan.tickets)
    return names


_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)


def _urls(plan: TripPlan) -> set[str]:
    """Every URL anywhere in a plan: a source, a booking link, a link in a note."""
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            found.update(url.rstrip(".,;:)") for url in _URL.findall(value))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(plan.model_dump(mode="json"))
    return found


_URL_PARTS = re.compile(r"([^:/?#]+)://([^/?#@]*@)?([^/?#]*)([^?#]*)(\??[^#]*)", re.IGNORECASE)


def _same_url(url: str) -> str:
    """A URL as a re-typed copy of it would still read: scheme and host in lower case, no trailing slash, no #fragment.

    The path and the query keep their case and every character, so a different page is a different URL."""
    match = _URL_PARTS.fullmatch(url.partition("#")[0])
    if match is None:
        return url
    scheme, userinfo, host, path, query = match.groups()
    return f"{scheme.lower()}://{userinfo or ''}{host.lower()}{path.rstrip('/')}{query}"


def _new_urls(original: TripPlan, candidate: TripPlan) -> set[str]:
    """The URLs of a repaired plan that are not in the original, not counting a copy of one the original has."""
    known = {_same_url(url) for url in _urls(original)}
    return {url for url in _urls(candidate) if _same_url(url) not in known}


def _refusal(original: TripPlan, candidate: TripPlan) -> str | None:
    """Why a repaired plan is refused outright: it took a stop, hotel or ticket out of the plan, put one
    in, or brought a URL the original did not have. A repair fixes what is wrong; it adds nothing."""
    before, after = _names(original), _names(candidate)
    dropped = sorted((before - after).elements())
    if dropped:
        return "repair removed from the original plan: " + ", ".join(name for _, name in dropped[:5])
    if after - before or _new_urls(original, candidate):
        return "repair introduced a fact or source that was not in the original plan"
    return None


#: What a repair may change in each part of a plan: times, the dates of a stay or a trip leg, and
#: wording. Every other value is the original's (see `_restore_facts`).
_EDITABLE: dict[type, frozenset[str]] = {
    TripPlan: frozenset({"summary", "change_summary", "days", "hotels", "tickets"}),
    DayPlan: frozenset({"title", "note", "items"}),
    PlanItem: frozenset({"start", "end", "notes"}),
    HotelStay: frozenset({"check_in", "check_out", "notes"}),
    TicketOption: frozenset(
        {"depart_at", "arrive_at", "duration_minutes", "return_depart_at", "return_arrive_at", "return_duration_minutes", "notes"}
    ),
}

Row = TypeVar("Row", PlanItem, HotelStay, TicketOption)


def _keep(before: Any, after: Any) -> None:
    """Give `after` the original's value for every field of it that a repair may not change."""
    editable = _EDITABLE[type(after)]
    for name in type(after).model_fields:
        if name not in editable:
            setattr(after, name, copy.deepcopy(getattr(before, name)))


def _pairs(before: list[Row], after: list[Row]) -> list[tuple[Row, Row]]:
    """Each row of a repaired plan with the original row it stands for: the one with its id and name,
    else the next unmatched one with its name, so a plan that repeats a name keeps each row's own values."""
    free = list(before)
    pairs: list[tuple[Row, Row]] = []
    unmatched: list[Row] = []
    for row in after:
        index = next((i for i, old in enumerate(free) if (old.id, old.name) == (row.id, row.name)), None)
        if index is None:
            unmatched.append(row)
        else:
            pairs.append((free.pop(index), row))
    for row in unmatched:
        index = next((i for i, old in enumerate(free) if old.name == row.name), None)
        if index is not None:
            pairs.append((free.pop(index), row))
    return pairs


def _restore_facts(original: TripPlan, candidate: TripPlan) -> None:
    """A repair re-shapes the plan; it cannot fetch, price, locate or rewrite anything. Every value it
    has no business with is put back from the original, so a fact the writer altered, made up or left
    out (a fetch time, the sources, a price, a point on the map, the forecast, the data gaps) is as it was."""
    _keep(original, candidate)
    days = {day.date: day for day in original.days}
    for day in candidate.days:
        if day.date in days:
            _keep(days[day.date], day)
        else:
            # A day the original did not have has no forecast or ascent: the writer cannot supply one.
            day.weather = None
            day.elevation_gain_m = None
    for old_rows, new_rows in (
        ([item for day in original.days for item in day.items], [item for day in candidate.days for item in day.items]),
        (original.hotels, candidate.hotels),
        (original.tickets, candidate.tickets),
    ):
        for before, after in _pairs(old_rows, new_rows):
            _keep(before, after)
    _settle_ids(candidate)
    _settle_forecasts(candidate)


def _settle_ids(plan: TripPlan) -> None:
    """Rows that came back sharing an id after the originals' ids were put back get distinct ones.

    The ids are put back from the original so a repair cannot re-label a row, but an original that held one id twice
    (a plan that did not come from the planner) would then never validate: the writer's own unique ids are overwritten.
    The first row keeps the id; each later one takes the id with a number after it, one nothing else in the plan has."""
    rows = [*(item for day in plan.days for item in day.items), *plan.hotels, *plan.tickets]
    taken = {row.id for row in rows}
    seen: set[str] = set()
    for row in rows:
        if row.id in seen:
            number = 2
            while f"{row.id}-{number}" in taken:
                number += 1
            row.id = f"{row.id}-{number}"
            taken.add(row.id)
        seen.add(row.id)


def _settle_forecasts(plan: TripPlan) -> None:
    """A forecast on a day that is not its date goes to the day it is for, or is dropped.

    The forecast is put back from the original, so a writer cannot correct it. Moving a forecast the plan already
    holds to its own date invents nothing. One that has no day to go to (or whose day has a forecast) is removed.
    A day left without a forecast gets a gap that says so, and lets the next refresh ask the source again."""
    days = {day.date: day for day in plan.days}
    strays = [(day, weather) for day in plan.days if (weather := day.weather) is not None and weather.date != day.date]
    for day, _ in strays:
        day.weather = None
    for day, weather in strays:
        home = days.get(weather.date)
        if home is not None and home.weather is None:
            home.weather = weather
    for day, weather in strays:
        if day.weather is None:
            plan.data_quality.gaps.append(
                DataGap(
                    section="weather",
                    kind=FailureKind.NO_DATA,
                    message=f"The forecast filed under {day.date} was for {weather.date}, so {day.date} has none.",
                    retryable=True,
                )
            )


def is_edit_outcome(gap: DataGap) -> bool:
    """A gap that says what an edit could not do (no stop found, a removal that matched none, two stops that
    could not be told apart, a place already in the plan, nothing cheaper came back), not what the plan lacks.
    The reply to that message tells it; the plan does not keep it. The messages are `ModifyPath`'s: each of its
    attraction outcomes says the itinerary or the stop "stayed as it was", or that a place "cannot be added twice"."""
    text = f"{gap.section} {gap.message}".lower()
    if gap.section == "hotels":
        return any(phrase in text for phrase in ("no cheaper verified hotel", "no grander verified hotel", "no other verified hotel"))
    return gap.section == "attractions" and any(
        phrase in text
        for phrase in (
            "could not find",
            "no alternative stop",
            "no verified stop",
            "stayed as it was",
            "cannot be added twice",
        )
    )


def tidy_data_gaps(plan: TripPlan, *, drop_edit_gaps: bool = False) -> TripPlan:
    """Drop obsolete or duplicated gaps before a plan is returned.

    A gap is a statement about the current plan, not an append-only log. Old
    Wikimedia failures from before the policy fix, a missing origin that is now
    simply optional, and a weather gap about days that now carry a seasonal or
    forecast row must not survive into a new version. A forecast that failed is
    the exception: its gap stays until a live forecast covers every date.
    """
    all_weather = bool(plan.days) and all(
        day.weather is not None and day.weather.status != DataStatus.UNAVAILABLE
        for day in plan.days
    )
    # A failed forecast stays on the record while a seasonal reference stands in for it.
    all_live = all_weather and all(day.weather is not None and day.weather.status == DataStatus.OK for day in plan.days)
    kept: list[Any] = []
    seen: set[tuple[str, str]] = set()
    for gap in plan.data_quality.gaps:
        text = f"{gap.section} {gap.message}".lower()
        if gap.section == "notes":
            continue
        if drop_edit_gaps and is_edit_outcome(gap):
            # These describe a past edit attempt, not the current plan's data.
            continue
        if "wikipedia-geosearch" in text or "robot policy" in text or "w.wiki" in text:
            # Obsolete Wikimedia fetch failures; the runtime now uses a
            # compliant UA and a keyless OSM fallback before Wikivoyage.
            continue
        if "no origin city was given" in text or "ticket agent returned no usable data" in text:
            continue
        if gap.section == "weather" and (all_live or (all_weather and gap.kind is FailureKind.NO_DATA)):
            continue
        if gap.section == "weather" and "no usable data" in text:
            gap.message = (
                f"No usable weather for {plan.dates.start} to {plan.dates.end}: "
                "the dates are outside the 16-day forecast window and no seasonal "
                "reference was returned."
            )
        key = (gap.section, gap.message)
        if key in seen:
            continue
        seen.add(key)
        kept.append(gap)
    plan.data_quality.gaps = kept
    return plan


def _span(start: date, end: date) -> str:
    return f"{start.isoformat()} – {end.isoformat()}"


def _rate(price: Money | None) -> str:
    return f"{price.currency} {price.amount:,.2f}".rstrip("0").rstrip(".") if price else "no price"


def _forecast(plan: TripPlan) -> list[dict[str, Any] | None]:
    """Each day's forecast as the traveller sees it: a fetch time alone is not a change."""
    return [day.weather.model_dump(mode="json", exclude={"fetched_at"}) if day.weather else None for day in plan.days]


def diff_plans(old: TripPlan | None, new: TripPlan) -> list[dict[str, Any]]:
    if old is None:
        return [{"kind": "created", "detail": f"{new.item_count} items"}]
    old_items = {item.id: item for _, item in old.iter_items()}
    new_items = {item.id: item for _, item in new.iter_items()}
    changes: list[dict[str, Any]] = []
    for item_id, item in new_items.items():
        if item_id not in old_items:
            changes.append({"kind": "added", "item_id": item_id, "name": item.name})
        elif old_items[item_id].model_dump() != item.model_dump():
            changes.append(
                {
                    "kind": "changed",
                    "item_id": item_id,
                    "name": item.name,
                    "from": old_items[item_id].name,
                }
            )
    for item_id, item in old_items.items():
        if item_id not in new_items:
            changes.append({"kind": "removed", "item_id": item_id, "name": item.name})
    # The trip itself: its range and the date of each day. Stops keep their ids when a trip moves, so
    # without this a move to other dates shows no change at all.
    if (old.dates, [day.date for day in old.days]) != (new.dates, [day.date for day in new.days]):
        changes.append(
            {
                "kind": "dates",
                "from": _span(old.dates.start, old.dates.end),
                "to": _span(new.dates.start, new.dates.end),
            }
        )
    # Who travels, from where and on what budget: the details the page's "generate again" carries.
    if old.party_size != new.party_size:
        changes.append({"kind": "party", "from": old.party_size, "to": new.party_size})
    if (old.origin or "") != (new.origin or ""):
        changes.append({"kind": "origin", "from": old.origin or "", "to": new.origin or ""})
    if old.budget != new.budget:
        changes.append({"kind": "budget", "from": _rate(old.budget), "to": _rate(new.budget)})
    old_hotel = old.first_stay.name if old.hotels else ""
    new_hotel = new.first_stay.name if new.hotels else ""
    if old_hotel != new_hotel:
        changes.append({"kind": "hotel", "from": old_hotel, "to": new_hotel})
    elif old.hotels and new.hotels:
        # The same hotel, kept or priced again: its nights and its rate are not a different hotel.
        before, after = old.first_stay, new.first_stay
        if (before.check_in, before.check_out) != (after.check_in, after.check_out):
            changes.append(
                {
                    "kind": "stay",
                    "name": after.name,
                    "from": _span(before.check_in, before.check_out),
                    "to": _span(after.check_in, after.check_out),
                }
            )
        if before.price_per_night != after.price_per_night:
            changes.append(
                {
                    "kind": "hotel_price",
                    "name": after.name,
                    "from": _rate(before.price_per_night),
                    "to": _rate(after.price_per_night),
                }
            )
    old_flight = old.tickets[0].name if old.tickets else ""
    new_flight = new.tickets[0].name if new.tickets else ""
    if old_flight != new_flight:
        changes.append({"kind": "flight", "from": old_flight, "to": new_flight})
    if _forecast(old) != _forecast(new):
        changes.append({"kind": "weather"})
    if (old.cost.total, old.cost.status) != (new.cost.total, new.cost.status):
        changes.append(
            {
                "kind": "cost",
                "from": old.cost.total,
                "to": new.cost.total,
                "status": new.cost.status,
                "from_currency": old.cost.currency,
                "currency": new.cost.currency,
            }
        )
    return changes
