"""Disruption and error recovery for a plan that is already written.

A tool failure, a closed venue or a flight that no longer exists is not a new trip and not an
ordinary edit: the rest of the plan is still true, and only the broken segment is in question. This
module makes that a first-class path instead of a data gap on the side (upgrade note, step 4):

1. The broken segment is found and marked (a closed stop, a missing fare).
2. Only the agent that owns that segment is called for alternatives.
3. The constraints are checked again.
4. Everything else is preserved - locked and confirmed stops are never touched.
5. The plan says, in the reply, what changed and why.

The classifier is deterministic on purpose: "Tokyo Skytree is closed tomorrow" has one meaning, and
it must not depend on a model deciding that a closed stop is an edit. A question ("what if it's
closed?") is not a disruption and falls through to the ask path.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from app.application.router import is_replan_message, looks_like_question
from app.domain.errors import CanaryLeak
from app.domain.models import (
    AgentName,
    DataStatus,
    Disruption,
    GeoPoint,
    ItemType,
    PlanItem,
    TripContext,
    TripPlan,
)

logger = logging.getLogger(__name__)

#: Ways a traveller says a place is not open. Kept to a closed place, not to "open late" or "closed on Mondays".
#: The state is its own group, so a summary says "is closed", not "is is closed".
_CLOSED = re.compile(
    r"\b(?:is|are|will be|was|were|has|have|had)?\s*"
    r"(closed|shut|shutdown|shut\s+down|"
    r"no\s+longer\s+open|not\s+(?:open|operating|running)|"
    r"permanently\s+closed|temporarily\s+closed|"
    r"under\s+(?:renovation|construction)|being\s+renovated)\b",
    re.IGNORECASE,
)
#: The words that make "closed" a real change rather than a hypothetical. A question is not one.
_HYPOTHETICAL = re.compile(
    r"\b(?:what\s+if|suppose|supposing|imagine|would|could|should|might|whether)\b", re.IGNORECASE
)
#: An explicit removal is the ordinary edit path's job: a replacement would contradict the request.
_REMOVE = re.compile(r"\b(?:remove|delete|drop|take\s+out|leave\s+out)\b", re.IGNORECASE)
#: A request for another stop is the ordinary edit path's job too. That path already records the closure,
#: searches a replacement and re-times the day's transfers; the recovery stage is for a bare report of a
#: closure that names no replacement.
_REPLACE = re.compile(
    r"\b(?:replace|swap|instead|alternative|another\s+(?:stop|place|option|suggestion|one)|"
    r"somewhere\s+else|other\s+suggestion|change\s+it\s+to)\b",
    re.IGNORECASE,
)

#: The shortest name fragment worth matching on. A one-word stop name still matches whole.
_MIN_FRAGMENT = 4


@dataclass(frozen=True)
class RecoveryRequest:
    """What a disruption asks the modify path to do, before any tool is called."""

    kind: str
    item_id: str
    name: str
    day: int | None = None
    reason: str = ""


def _named_item(plan: TripPlan, message: str) -> tuple[Any, PlanItem] | None:
    """The one stop the message names, the longest name first so "Tokyo Skytree" beats "Tokyo"."""
    text = (message or "").casefold()
    found: list[tuple[int, Any, PlanItem]] = []
    for entry in plan.days:
        for item in entry.items:
            name = (item.name or "").strip()
            if len(name) < 3:
                continue
            folded = name.casefold()
            head = folded.split("(")[0].strip()
            if folded in text or (len(head) >= _MIN_FRAGMENT and head in text):
                found.append((len(folded), entry, item))
    if not found:
        return None
    _length, day, item = max(found, key=lambda row: row[0])
    return day, item


def classify_closed_stop(plan: TripPlan, message: str, *, scope_item_id: str | None = None) -> RecoveryRequest | None:
    """A stop the message says is closed, if it is on the plan and the sentence is not a question.

    The selected row counts too ("this is closed tomorrow" with the stop selected). A message that only
    asks what would happen is not a disruption: it is answered, not acted on.
    """
    if not message or not _CLOSED.search(message):
        return None
    if looks_like_question(message) or is_replan_message(message) or _HYPOTHETICAL.search(message):
        return None
    if _REMOVE.search(message):
        # "Remove Nijo Castle, it's closed": the ordinary edit path removes it. Recovering with an
        # alternative here would contradict the sentence.
        return None
    if _REPLACE.search(message):
        # "The Peak Tram is closed. Please replace it." names what to do next; the edit path does that,
        # and it already records the closure while it re-times the day's transfers.
        return None
    named = _named_item(plan, message)
    if named is None and scope_item_id:
        named = plan.find_item(scope_item_id)
    if named is None:
        return None
    day, item = named
    if item.type is not ItemType.ATTRACTION:
        # A hotel or a meal is not "closed" in the way a visit is: those are edits, and their own paths ask.
        return None
    match = _CLOSED.search(message)
    return RecoveryRequest(
        kind="closed_stop",
        item_id=item.id,
        name=item.name,
        day=plan.days.index(day) + 1,
        reason=(match.group(1).strip().lower() if match else "closed"),
    )


def _row_for(candidate: Any) -> dict[str, Any]:
    """The place row a source returned, as the plan's own candidate shape."""
    if hasattr(candidate, "model_dump"):
        return candidate.model_dump(mode="json")
    return dict(candidate)


def _already_in_plan(plan: TripPlan, row: dict[str, Any]) -> bool:
    name = str(row.get("name") or "").casefold()
    return any((item.name or "").casefold() == name for _, item in plan.iter_items())


def _nearest(candidates: list[dict[str, Any]], anchor: GeoPoint | None) -> dict[str, Any] | None:
    """The alternative closest to the closed stop, so the day stays one neighbourhood."""
    usable = [row for row in candidates if row.get("geo")]
    if not usable:
        return None
    if anchor is None:
        return usable[0]
    from app.infrastructure.http import haversine_km

    def distance(row: dict[str, Any]) -> float:
        geo = row.get("geo") or {}
        return haversine_km(anchor.lat, anchor.lng, float(geo.get("lat") or 0), float(geo.get("lng") or 0))

    return min(usable, key=distance)


async def recover_closed_stop(
    modify_path: Any,
    plan: TripPlan,
    request: RecoveryRequest,
    *,
    context: TripContext,
    toolbox: Any,
    tracer: Any,
    emit: Any,
) -> Any:
    """Recover one closed stop: ask the attraction agent only, keep every other row, and say what changed.

    A locked or confirmed stop is never moved: the plan keeps it and says why. A replacement is the
    nearest fresh candidate the place source returned; no candidate means the stop is removed with a
    note. The result is an ``EditResult`` so the ordinary modify tail persists and phrases it.
    """
    from app.application.modification import EditResult, _apply_candidate, _recheck
    from app.application.trip_rules import blocked_dates, travel_windows
    from app.application.validation import diff_plans, repair_plan, validate_plan

    day_index: int | None = None
    for index, day in enumerate(plan.days):
        if any(row.id == request.item_id for row in day.items):
            day_index = index
            break
    if day_index is None:
        return None

    reason = request.reason or "closed"
    day = plan.days[day_index]
    item = next((row for row in day.items if row.id == request.item_id), None)
    if item is None:
        return None
    if item.locked or item.confirmed:
        unchanged = plan.model_copy(deep=True)
        kind = "locked" if item.locked else "confirmed"
        unchanged.change_summary = (
            f"{item.name} is {reason}, but it is your {kind} stop, so it stays on the plan. "
            "Nothing else was changed."
        )
        if unchanged.change_summary not in unchanged.data_quality.notes:
            unchanged.data_quality.notes.append(unchanged.change_summary)
        return EditResult(unchanged, [], scope="stop", day=day_index + 1)

    updated = plan.model_copy(deep=True)
    destination = None
    try:
        destination = await modify_path.plan_path._locate(context.destination, toolbox=toolbox, tracer=tracer)
    except Exception:
        logger.warning("recovery could not locate %s", context.destination, exc_info=True)

    candidates: list[dict[str, Any]] = []
    if destination is not None:
        results: dict[str, Any] = {}
        try:
            with tracer.span("recovery.agents", parts="attraction"):
                results = await modify_path.plan_path.run_agents(
                    context=context,
                    destination=destination,
                    date_range=context.date_range() or plan.dates,
                    toolbox=toolbox,
                    emit=emit,
                    names={AgentName.ATTRACTION},
                    extra={
                        "places_query": f"attractions near {item.name} {context.destination}".strip(),
                        "anchor_geo": _anchor(item),
                        "places_limit": 12,
                    },
                )
        except CanaryLeak:
            raise
        except Exception:
            logger.warning("recovery could not ask for alternatives", exc_info=True)
        attraction = results.get(AgentName.ATTRACTION) or results.get("attraction")
        candidates = (getattr(attraction, "payload", {}) or {}).get("places") or []
        for gap in getattr(attraction, "failures", None) or []:
            if not any(
                known.section == gap.section and known.message == gap.message
                for known in updated.data_quality.gaps
            ):
                updated.data_quality.gaps.append(gap)

    live = [row for row in candidates if not _already_in_plan(plan, row)]
    chosen = _nearest([_row_for(row) for row in live], item.geo)
    target_day = updated.days[day_index]
    target = next((row for row in target_day.items if row.id == request.item_id), None)
    if target is None:
        return None
    if chosen is not None:
        _apply_candidate(target, chosen, target_day.date)
        target.data_status = DataStatus.OK
        summary = (
            f"{request.name} is {reason}, so it was replaced with {target.name} nearby. "
            "Everything else is unchanged."
        )
        disruption = Disruption(
            kind="closure",
            summary=summary,
            affected_item_ids=[target.id],
            resolution=f"Replaced with {target.name} from the place source.",
            resolved=True,
        )
    else:
        target_day.items = [row for row in target_day.items if row.id != request.item_id]
        summary = (
            f"{request.name} is {reason} and no verified alternative came back, so it was taken off "
            f"day {day_index + 1}. Everything else is unchanged."
        )
        disruption = Disruption(
            kind="closure",
            summary=summary,
            affected_item_ids=[request.item_id],
            resolution="Removed; no alternative was returned by the place source.",
            resolved=True,
        )
    updated.disruptions.append(disruption)
    if summary not in updated.data_quality.notes:
        updated.data_quality.notes.append(summary)
    if chosen is not None:
        # The day has a different stop, so the transfers between its rows are worked out again. Without
        # this the new row would carry no leg, and a leg estimate is not a grounded transfer time.
        await _refresh_day_transfers(modify_path, plan, updated, context, toolbox)

    # Step 3: the plan's own checks read again (the day's clock and geography changed).
    rule_texts = [*context.hard_constraints, *context.preferences]
    plan_dates = [entry.date for entry in updated.days]
    blocked = blocked_dates(rule_texts, plan_dates)
    updated.constraints = _recheck(updated, context, blocked, travel_windows(rule_texts, plan_dates))
    errors = validate_plan(updated, modify_path.settings)
    if errors:
        gateway = getattr(modify_path, "gateway", None)
        if gateway is None or not getattr(gateway, "available", False):
            # There is no writer to repair with, and a plan that does not validate is not written: the
            # ordinary edit path reads the message instead. A visible failure, not a broken plan.
            logger.info("recovery left a plan that does not validate: %s", errors[:3])
            return None
        try:
            updated = await repair_plan(updated, errors, gateway, modify_path.settings)
        except Exception:
            logger.warning("recovery could not repair the changed plan", exc_info=True)
            return None
        if validate_plan(updated, modify_path.settings):
            logger.info("recovery's plan still does not validate; leaving the plan as it was")
            return None
    updated.change_summary = summary
    diff = diff_plans(plan, updated)
    if diff:
        # A recovery that moved a stop is a new version, as any edit that changed the plan is.
        updated.version = plan.version + 1
    return EditResult(updated, diff, scope="stop", day=day_index + 1)


def _anchor(item: PlanItem) -> dict[str, float] | None:
    """The closed stop's coordinates, in the shape the attraction agent's ``anchor_geo`` takes."""
    if item.geo is None:
        return None
    return {"lat": item.geo.lat, "lng": item.geo.lng}


async def _refresh_day_transfers(
    modify_path: Any,
    before: TripPlan,
    updated: TripPlan,
    context: TripContext,
    toolbox: Any,
) -> None:
    """Re-time the day's legs after a stop was swapped, and note where two stops now sit too close.

    The transfer source is the registry's own; an edit in the ordinary path does exactly this. A
    failure here leaves the plan as it is: transfers are an enrichment, not the recovery's point.
    """
    from app.application.modification import _gap, _tight_hops
    from app.application.planner import PACE_BUFFER_MINUTES, _pace
    from app.application.transfers import refresh_transfers

    try:
        registry = getattr(modify_path.plan_path, "registry", None)
        await refresh_transfers(
            before,
            updated,
            toolbox=toolbox,
            source=getattr(registry, "transfers", None),
            buffer=PACE_BUFFER_MINUTES.get(_pace(context), 5),
        )
    except CanaryLeak:
        raise
    except Exception:
        logger.warning("recovery could not refresh the day's transfers", exc_info=True)
        return
    updated.data_quality.gaps += [_gap("transfers", text) for text in _tight_hops(updated)]


def note_missing_flight(
    plan: TripPlan,
    *,
    date_changed: bool,
    flights: list[Any],
) -> TripPlan:
    """Say, on the plan and in the reply, that a trip moved to new dates has no flight for them.

    This is the second disruption the recovery path covers: the stay and the days moved, the fares did
    not come back, and the traveller must be told instead of finding an empty flights section. Nothing
    else is touched, and the section's own gap is kept: this adds the record and the sentence, it does
    not replace the evidence that the source returned nothing.
    """
    if not date_changed or plan.tickets or flights:
        return plan
    updated = plan.model_copy(deep=True)
    summary = (
        "No flight for the new dates came back, so the trip has no fare for them yet. "
        "The stay and the days were moved; add flights again when a source has them."
    )
    if not any(disruption.kind == "missing_flights" for disruption in updated.disruptions):
        updated.disruptions.append(
            Disruption(
                kind="missing_flights",
                summary=summary,
                affected_item_ids=[],
                resolution="Left empty; the source returned no fare for the new dates.",
                resolved=False,
            )
        )
    if summary not in updated.data_quality.notes:
        updated.data_quality.notes.append(summary)
    return updated
