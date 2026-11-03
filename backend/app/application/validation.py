"""Plan validation and bounded repair.

Validation is deliberately boring and explicit. Repair may only rename/re-shape
what is already present: a repaired plan cannot introduce a new attraction or a
new source URL that was not in the original document.
"""

from __future__ import annotations

import copy
import json
import re
from collections import Counter
from datetime import time
from typing import Any, TypeVar

from app.application.llm_gateway import LLMGateway
from app.config import Settings
from app.domain.errors import CanaryLeak, PlanInvalidError
from app.domain.models import DataStatus, DayPlan, HotelStay, PlanItem, TicketOption, TripPlan


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
    for _ in range(settings.max_repairs):
        user = (
            "Validation error:\n"
            + "\n".join(f"- {error}" for error in last_errors)
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


def _refusal(original: TripPlan, candidate: TripPlan) -> str | None:
    """Why a repaired plan is refused outright: it took a stop, hotel or ticket out of the plan, put one
    in, or brought a URL the original did not have. A repair fixes what is wrong; it adds nothing."""
    before, after = _names(original), _names(candidate)
    dropped = sorted((before - after).elements())
    if dropped:
        return "repair removed from the original plan: " + ", ".join(name for _, name in dropped[:5])
    if after - before or _urls(candidate) - _urls(original):
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
    for old_rows, new_rows in (
        ([item for day in original.days for item in day.items], [item for day in candidate.days for item in day.items]),
        (original.hotels, candidate.hotels),
        (original.tickets, candidate.tickets),
    ):
        for before, after in _pairs(old_rows, new_rows):
            _keep(before, after)


def tidy_data_gaps(plan: TripPlan, *, drop_edit_gaps: bool = False) -> TripPlan:
    """Drop obsolete or duplicated gaps before a plan is returned.

    A gap is a statement about the current plan, not an append-only log. Old
    Wikimedia failures from before the policy fix, a missing origin that is now
    simply optional, and a weather gap whose days now carry a seasonal/forecast
    row must not survive into a new version.
    """
    all_weather = bool(plan.days) and all(
        day.weather is not None and day.weather.status != DataStatus.UNAVAILABLE
        for day in plan.days
    )
    kept: list[Any] = []
    seen: set[tuple[str, str]] = set()
    for gap in plan.data_quality.gaps:
        text = f"{gap.section} {gap.message}".lower()
        if gap.section == "notes":
            continue
        if drop_edit_gaps and gap.section == "attractions" and any(
            phrase in text
            for phrase in ("could not find", "no alternative stop", "no verified stop")
        ):
            # These describe a past edit attempt, not the current plan's data.
            continue
        if "wikipedia-geosearch" in text or "robot policy" in text or "w.wiki" in text:
            # Obsolete Wikimedia fetch failures; the runtime now uses a
            # compliant UA and a keyless OSM fallback before Wikivoyage.
            continue
        if "no origin city was given" in text or "ticket agent returned no usable data" in text:
            continue
        if gap.section == "weather" and all_weather:
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
    old_hotel = old.hotels[0].name if old.hotels else ""
    new_hotel = new.hotels[0].name if new.hotels else ""
    if old_hotel != new_hotel:
        changes.append({"kind": "hotel", "from": old_hotel, "to": new_hotel})
    old_flight = old.tickets[0].name if old.tickets else ""
    new_flight = new.tickets[0].name if new.tickets else ""
    if old_flight != new_flight:
        changes.append({"kind": "flight", "from": old_flight, "to": new_flight})
    if (old.cost.total, old.cost.status) != (new.cost.total, new.cost.status):
        changes.append(
            {
                "kind": "cost",
                "from": old.cost.total,
                "to": new.cost.total,
                "status": new.cost.status,
                "currency": new.cost.currency,
            }
        )
    return changes
