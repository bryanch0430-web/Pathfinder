"""Plan validation and bounded repair.

Validation is deliberately boring and explicit. Repair may only rename/re-shape
what is already present: a repaired plan cannot introduce a new attraction or a
new source URL that was not in the original document.
"""

from __future__ import annotations

import json
from datetime import time
from typing import Any

from app.application.llm_gateway import LLMGateway
from app.config import Settings
from app.domain.errors import CanaryLeak, PlanInvalidError
from app.domain.models import DataStatus, TripPlan


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
    allowed_names = _names(plan)
    allowed_urls = {
        ref.url
        for day in plan.days
        for item in day.items
        for ref in item.source_refs
    }
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
        dropped = sorted(allowed_names - _names(candidate))
        if dropped:
            # A repair fixes what is wrong; it does not get to take a stop out of the plan.
            last_errors = shown = ["repair removed from the original plan: " + ", ".join(dropped[:5])]
            continue
        _restore_provenance(original, candidate)
        if not _grounded(candidate, allowed_names, allowed_urls):
            last_errors = shown = ["repair introduced a fact or source that was not in the original plan"]
            continue
        remaining = validate_plan(candidate, settings)
        if not remaining:
            return candidate
        last_errors = shown = remaining
        plan = candidate
    raise PlanInvalidError(
        "The plan still fails validation after bounded repair: " + "; ".join(shown[:3]),
    )


def _names(plan: TripPlan) -> set[str]:
    """Every stop, hotel and ticket name in a plan."""
    names = {item.name for day in plan.days for item in day.items}
    names |= {hotel.name for hotel in plan.hotels}
    names |= {ticket.name for ticket in plan.tickets}
    return names


def _restore_provenance(original: TripPlan, candidate: TripPlan) -> None:
    """A repair re-shapes the plan; it cannot fetch anything. Fetch times come from the original
    by name (or date, for the forecast), and sources the writer left out are put back."""
    items = {item.name: item for day in original.days for item in day.items}
    for day in candidate.days:
        for item in day.items:
            before = items.get(item.name)
            if before is None:
                continue
            item.fetched_at = before.fetched_at
            if "source_refs" not in item.model_fields_set:
                item.source_refs = list(before.source_refs)
    for kept, rows in ((original.hotels, candidate.hotels), (original.tickets, candidate.tickets)):
        by_name = {row.name: row for row in kept}
        for row in rows:
            before = by_name.get(row.name)
            if before is None:
                continue
            row.fetched_at = before.fetched_at
            if "source_refs" not in row.model_fields_set:
                row.source_refs = list(before.source_refs)
    weather = {day.date: day.weather for day in original.days if day.weather is not None}
    for day in candidate.days:
        before_weather = weather.get(day.date)
        if day.weather is not None and before_weather is not None:
            day.weather.fetched_at = before_weather.fetched_at


def _grounded(plan: TripPlan, allowed_names: set[str], allowed_urls: set[str]) -> bool:
    names = _names(plan)
    if not names.issubset(allowed_names):
        return False
    for day in plan.days:
        for item in day.items:
            for ref in item.source_refs:
                if ref.url and ref.url not in allowed_urls:
                    return False
    return True


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
