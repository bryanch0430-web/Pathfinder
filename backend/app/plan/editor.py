"""Minimal edits: the model proposes typed operations, code applies them.

The traveller sees the result as ticks ("swap hotel", "move stop"), so the edit must
be small, preserving locked and confirmed items and leaving everything unmentioned
untouched.  Nothing here re-plans the trip: a rebuild is an explicit, separate
decision the model can only recommend.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.config import Settings
from app.core.dates import shift_time
from app.core.llm import LLMClient
from app.core.prompts import context_brief, plan_outline, skill
from app.core.trace import TurnTrace
from app.domain.models import DataGap, DayPlan, PlanItem, TripContext, TripPlan
from app.evidence.models import Bundle, Evidence
from app.evidence.providers import Providers
from app.plan.agents import candidate_pool
from app.plan.builder import _item_from_evidence, refresh_derived
from app.understand.schemas import EditDraft, StopChoice

REBUILD_FIELDS = {"start_date", "end_date", "days", "destination", "origin", "stops"}


@dataclass
class EditOutcome:
    plan: TripPlan
    context: TripContext
    draft: EditDraft
    log: list[str] = field(default_factory=list)
    rebuild: bool = False
    rebuild_reason: str = ""
    changed: bool = False
    research_failed: bool = False


async def propose_edit(
    *,
    llm: LLMClient,
    providers: Providers,
    settings: Settings,
    context: TripContext,
    plan: TripPlan,
    bundle: Bundle,
    message: str,
    scope_label: str | None,
    scope_item_id: str | None,
    trace: TurnTrace | None = None,
    skill_overrides: dict[str, str] | None = None,
) -> EditOutcome:
    log: list[str] = []
    research_failed = False
    draft = await _ask(llm, context, plan, bundle, message, scope_label, scope_item_id, skill_overrides, trace)
    if draft.needs_research:
        extra = await _research(providers, context, draft, trace)
        if extra:
            for index, item in enumerate(extra):
                item.ref = f"x{index + 1}"
            bundle.add(extra, "edit_research")
            log.append(f"Found a source for {extra[0].name}.")
        else:
            wanted = draft.research_name or "that place"
            research_failed = True
            log.append(f"No source found for {wanted}; nothing was added.")
        draft = await _ask(
            llm,
            context,
            plan,
            bundle,
            message,
            scope_label,
            scope_item_id,
            skill_overrides,
            trace,
            research={
                "attempted": True,
                "found": len(extra),
                "names": [item.name for item in extra][:6],
                "name": draft.research_name,
            },
        )
        if draft.needs_research and extra:
            research_failed = True
            log.append(f"The sources found {extra[0].name}, but the edit still asked for another lookup.")
    if draft.scope == "whole" or draft.rebuild_reason:
        return EditOutcome(
            plan=plan,
            context=context,
            draft=draft,
            rebuild=True,
            rebuild_reason=draft.rebuild_reason or "the change is too broad to apply as a small edit",
        )
    updated_plan = plan.model_copy(deep=True)
    updated_context = context.model_copy(deep=True)
    changed = False
    applied: list[str] = []
    refused: list[str] = []
    for op in draft.ops:
        before = len(log)
        done = False
        try:
            done = _apply(op, updated_plan, updated_context, bundle, log)
        except Exception as exc:  # noqa: BLE001 - one bad op must not lose the others
            log.append(f"Skipped a step ({op.op}): {exc}")
        changed |= done
        if op.op != "note":
            (applied if done else refused).extend(log[before:])
    if _dates_touched(draft):
        return EditOutcome(
            plan=plan,
            context=updated_context,
            draft=draft,
            rebuild=True,
            rebuild_reason="the dates or destination changed, so the itinerary is rebuilt",
            log=log,
        )
    if changed:
        refresh_derived(updated_plan, updated_context)
        actual = actual_changes(plan, updated_plan, context, updated_context)
        if not actual:
            # Every step said it worked, yet the candidate equals the current plan.
            changed = False
            log[:] = [*refused, "Nothing in the plan changed."]
        else:
            draft = _honest_draft(draft, applied, refused, actual)
            updated_plan.change_summary = draft.summary or "Edited the trip."
    return EditOutcome(
        plan=updated_plan,
        context=updated_context,
        draft=draft,
        log=log,
        changed=changed,
        research_failed=research_failed,
    )


def actual_changes(old: TripPlan, new: TripPlan, old_context: TripContext, new_context: TripContext) -> list[dict[str, Any]]:
    """What really differs between the current plan and the candidate (empty means no change)."""
    entries = diff_plans(old, new)
    if not any(entry["kind"] == "hotel" for entry in entries):
        before, after = [hotel.name for hotel in old.hotels], [hotel.name for hotel in new.hotels]
        if before != after:
            entries.append({"kind": "hotel", "name": after[0] if after else "", "from": before[:1], "to": after[:1]})
    if old_context.budget != new_context.budget:
        entries.append({"kind": "budget", "from": old_context.budget, "to": new_context.budget})
    if old_context.party_size != new_context.party_size:
        entries.append({"kind": "party", "from": old_context.party_size, "to": new_context.party_size})
    return entries


def wording_fits(text: str, entries: list[dict[str, Any]]) -> bool:
    """False when the text claims a removal, addition or stay swap the diff does not contain."""
    kinds = {entry["kind"] for entry in entries}
    low = (text or "").lower()
    if re.search(r"\b(remov\w*|delet\w*|drop\w*)\b", low) and "removed" not in kinds:
        return False
    if re.search(r"\b(add|adds|added|adding)\b", low) and "added" not in kinds:
        return False
    if (
        re.search(r"\b(hotel|stay)\b", low)
        and re.search(r"\b(swap\w*|switch\w*|replac\w*|chang\w*)\b", low)
        and "hotel" not in kinds
    ):
        return False
    return True


def _honest_draft(draft: EditDraft, applied: list[str], refused: list[str], entries: list[dict[str, Any]]) -> EditDraft:
    """Keep the model's wording only when it matches what was applied; otherwise state the facts."""
    if not refused and wording_fits(f"{draft.summary} {draft.detail}", entries):
        return draft
    return draft.model_copy(
        update={
            "summary": (" ".join(applied) or "Edited the trip.")[:180],
            "detail": " ".join(refused)[:240],
        }
    )


async def _ask(
    llm: LLMClient,
    context: TripContext,
    plan: TripPlan,
    bundle: Bundle,
    message: str,
    scope_label: str | None,
    scope_item_id: str | None,
    overrides: dict[str, str] | None,
    trace: TurnTrace | None,
    research: dict[str, Any] | None = None,
) -> EditDraft:
    payload = {
        "trip": context_brief(context),
        "plan": plan_outline(plan),
        "selected_stop": {"id": scope_item_id, "label": scope_label} if scope_item_id or scope_label else None,
        "request": message,
        "candidates": candidate_pool(bundle),
        "research": research or {"attempted": False, "found": 0},
        "locked": [
            {"id": item.id, "name": item.name}
            for day in plan.days
            for item in day.items
            if item.locked or item.confirmed
        ],
    }
    return await llm.json(
        purpose="edit",
        system=skill("edit", overrides),
        user=json.dumps(payload, ensure_ascii=False, indent=1),
        schema=EditDraft,
    )


async def _research(
    providers: Providers, context: TripContext, draft: EditDraft, trace: TurnTrace | None
) -> list[Evidence]:
    """Look for the kind of place the edit needs, through the same sourced providers."""
    city = context.destination or ""
    kind = draft.research_kind
    found: list[Evidence] = []
    named = (draft.research_name or "").strip()
    if named:
        found.extend(await providers.lookup_place(named, city))
    if kind == "hotel":
        found.extend(await providers.hotels(city, limit=8, style=context.hotel_style))
    elif kind == "meal":
        found.extend(await providers.meals(city, limit=8))
    elif kind == "ticket":
        from datetime import date, timedelta

        start = date.fromisoformat(context.start_date) if context.start_date else date.today() + timedelta(days=30)
        end = date.fromisoformat(context.end_date) if context.end_date else start + timedelta(days=int(context.days or 3) - 1)
        found.extend(
            await providers.tickets(
                context.origin, city, start, end, int(context.party_size or 1), currency=context.currency
            )
        )
    else:
        found.extend(await providers.attractions(city, limit=10, interests=[*context.preferences, *draft.research_queries]))
    deduped: dict[str, Evidence] = {}
    for item in found:
        deduped.setdefault(item.name.strip().lower(), item)
    result = list(deduped.values())
    if trace:
        trace.say(
            f"Looked for {kind} options for the edit"
            + (f" (“{named or draft.research_queries[0]}”)" if (named or draft.research_queries) else "")
            + f": {len(result)} sourced."
        )
    return result


def _apply(op, plan: TripPlan, context: TripContext, bundle: Bundle, log: list[str]) -> bool:
    if op.op == "note":
        if op.text:
            log.append(str(op.text))
        return False
    if op.op == "set_field":
        return _set_field(op, context, log)
    if op.op == "swap_hotel":
        evidence = bundle.get(op.ref or "")
        if evidence is None or evidence.kind != "hotel" or evidence.data_status == "unavailable":
            log.append("No sourced hotel matched that request, so the stay is unchanged.")
            return False
        if plan.hotels and (plan.hotels[0].locked or plan.hotels[0].confirmed):
            log.append("The stay is locked, so it was not replaced.")
            return False
        if plan.hotels and plan.hotels[0].name.strip().lower() == evidence.name.strip().lower():
            log.append("That is already the stay on the plan.")
            return False
        from app.plan.builder import _hotel_from_evidence

        hotel = _hotel_from_evidence(evidence)
        hotel.check_in = plan.hotels[0].check_in if plan.hotels else plan.dates.start
        hotel.check_out = plan.hotels[0].check_out if plan.hotels else plan.dates.end
        plan.hotels = [hotel]
        log.append(f"Swapped the stay to {hotel.name}.")
        return True
    if op.op == "swap_ticket":
        evidence = bundle.get(op.ref or "")
        if evidence is None or evidence.kind != "ticket":
            log.append("No sourced travel option matched that request.")
            return False
        from app.domain.models import TicketOption

        plan.tickets = [
            TicketOption(id="tk-edit", leg_id="leg-out", origin=plan.origin, destination=plan.destination.name, **evidence.to_ticket_kwargs())
        ]
        log.append(f"Switched the travel option to {evidence.name}.")
        return True

    target = _find(plan, op.item_id) if op.item_id else None
    if op.op in {"move", "retime", "remove", "replace"} and target is None:
        log.append("The stop to change was not found at that address, so it was left alone.")
        return False
    if target is not None and (target.locked or target.confirmed) and op.op in {"move", "retime", "remove", "replace"}:
        log.append(f"{target.name} is locked, so it was not changed.")
        return False

    if op.op == "move" and target is not None:
        day_index, day_plan = _day(plan, op.day or 1)
        if day_plan is None:
            return False
        _detach(plan, target.id)
        length = max(30, _span(target))
        target.start = op.start or target.start
        target.end = op.end or shift_time(target.start, length)
        day_plan.items.append(target)
        log.append(f"Moved {target.name} to day {day_index} at {target.start}.")
        return True

    if op.op == "retime" and target is not None:
        length = max(30, _span(target))
        target.start = op.start or target.start
        target.end = op.end or shift_time(target.start, length)
        log.append(f"Moved {target.name} to {target.start}.")
        return True

    if op.op == "remove" and target is not None:
        _detach(plan, target.id)
        log.append(f"Removed {target.name}.")
        return True

    if op.op in {"add", "replace"}:
        evidence = bundle.get(op.ref or "") if op.ref else None
        if evidence is None and (op.name or "").strip():
            return _add_user_provided(op, plan, log)
        if evidence is None or evidence.data_status == "unavailable":
            log.append("No sourced place matched that request, so nothing was added.")
            return False
        day_index, day_plan = _day(plan, op.day or 1)
        if day_plan is None:
            return False
        if op.op == "replace" and target is not None:
            day_index, day_plan = _day_of(plan, target.id)
            start = target.start
            end = target.end
            _detach(plan, target.id)
        else:
            start = op.start or "15:00"
            end = op.end or shift_time(start, evidence.suggested_minutes or 90)
        choice = StopChoice(ref=evidence.ref, start=start, end=end, note=op.note or "", meal_slot=None)
        item = _item_from_evidence(evidence, choice, len(day_plan.items))
        day_plan.items.append(item)
        day_plan.items.sort(key=lambda entry: entry.start or "99:99")
        verb = "Replaced" if op.op == "replace" else "Added"
        log.append(f"{verb} {item.name} on day {day_index} at {item.start}.")
        return True
    return False


def _add_user_provided(op, plan: TripPlan, log: list[str]) -> bool:
    """Add a named place the sources could not provide, clearly marked unconfirmed.

    The traveller asked for it explicitly; refusing would make the product unable to
    take a decision the human owns. The stop carries no invented address, hours or
    price, and the plan records a gap so the uncertainty stays visible.
    """
    name = str(op.name or "").strip()[:160]
    if not name:
        return False
    day_index, day_plan = _day(plan, op.day or 1)
    if day_plan is None:
        log.append("That day is not in the plan.")
        return False
    target = _find(plan, op.item_id) if op.item_id else None
    if target is not None:
        day_index, day_plan = _day_of(plan, target.id) or (day_index, day_plan)
        start = op.start or target.start or "13:00"
        end = op.end or target.end or "15:00"
        _detach(plan, target.id)
    else:
        start = op.start or "13:00"
        end = op.end or shift_time(start, 120)
    item_id = "usr-" + hashlib.sha1(name.lower().encode("utf-8")).hexdigest()[:10]
    existing = plan.item_index()
    if item_id in existing:
        item_id = f"{item_id}-{len(existing) + 1}"
    note = (op.note or "").strip()
    item = PlanItem(
        id=item_id,
        type="attraction",
        name=name,
        start=start,
        end=end,
        notes=(
            "Added at your request. The address, opening hours and ticket price are "
            "not confirmed by a source."
            + (" " + note if note else "")
        ),
        data_status="stale",
        tags=["user-provided"],
    )
    day_plan.items.append(item)
    day_plan.items.sort(key=lambda entry: entry.start or "99:99")
    plan.data_quality.gaps.append(
        DataGap(
            section="plan",
            kind="user_provided",
            message=f"{name} was added at the traveller's request without a confirmed source; check its address, hours and tickets.",
            retryable=True,
        )
    )
    log.append(f"Added {name} as an unconfirmed stop at {start}.")
    return True


def _set_field(op, context: TripContext, log: list[str]) -> bool:
    field = (op.field or "").strip()
    value = op.value
    if field in REBUILD_FIELDS:
        log.append("This changes the shape of the trip, so it is replanned.")
        if field == "budget" and value is not None:
            context.budget = float(value)
        if field == "party_size" and value is not None:
            context.party_size = max(1, int(value))
        return False
    if field == "budget" and value is not None:
        context.budget = float(value)
        log.append(f"Budget set to {context.budget:.0f} {context.currency}.")
        return True
    if field == "party_size" and value is not None:
        context.party_size = max(1, int(value))
        log.append(f"Party size set to {context.party_size}.")
        return True
    return False


def _dates_touched(draft: EditDraft) -> bool:
    return any(op.op == "set_field" and (op.field or "") in REBUILD_FIELDS for op in draft.ops)


def _find(plan: TripPlan, item_id: str | None):
    if not item_id:
        return None
    for day in plan.days:
        for item in day.items:
            if item.id == item_id:
                return item
    return None


def _day_of(plan: TripPlan, item_id: str):
    for index, day in enumerate(plan.days, start=1):
        if any(item.id == item_id for item in day.items):
            return index, day
    return 0, None


def _detach(plan: TripPlan, item_id: str) -> None:
    for day in plan.days:
        day.items = [item for item in day.items if item.id != item_id]


def _day(plan: TripPlan, index: int):
    if 1 <= index <= len(plan.days):
        return index, plan.days[index - 1]
    return 0, None


def _span(item) -> int:
    from app.core.dates import clock_minutes

    start, end = clock_minutes(item.start), clock_minutes(item.end)
    if start is None or end is None:
        return 60
    return max(30, end - start)


# -- diffing -----------------------------------------------------------


def diff_plans(old: TripPlan | None, new: TripPlan) -> list[dict[str, Any]]:
    if old is None:
        return [{"kind": "created", "detail": new.change_summary}]
    entries: list[dict[str, Any]] = []
    old_items = {item.id: (index, item) for index, day in enumerate(old.days, start=1) for item in day.items}
    new_items = {item.id: (index, item) for index, day in enumerate(new.days, start=1) for item in day.items}
    for item_id, (day, item) in new_items.items():
        if item_id not in old_items:
            entries.append({"kind": "added", "item_id": item_id, "name": item.name, "to": f"Day {day} {item.start}"})
            continue
        old_day, before = old_items[item_id]
        if old_day != day:
            entries.append({"kind": "changed", "item_id": item_id, "name": item.name, "from": f"Day {old_day}", "to": f"Day {day}"})
        elif (before.start, before.end) != (item.start, item.end):
            entries.append({"kind": "changed", "item_id": item_id, "name": item.name, "from": before.start, "to": item.start})
    for item_id, (day, item) in old_items.items():
        if item_id not in new_items:
            entries.append({"kind": "removed", "item_id": item_id, "name": item.name, "from": f"Day {day}"})
    if old.dates != new.dates:
        entries.append({"kind": "dates", "from": old.dates.start, "to": new.dates.start})
    old_hotel = old.hotels[0].id if old.hotels else ""
    new_hotel = new.hotels[0].id if new.hotels else ""
    if old_hotel != new_hotel:
        entries.append(
            {
                "kind": "hotel",
                "id": new_hotel,
                "name": new.hotels[0].name if new.hotels else "",
                "from": old.hotels[0].name if old.hotels else "",
                "to": new.hotels[0].name if new.hotels else "",
            }
        )
    old_ticket = old.tickets[0].id if old.tickets else ""
    new_ticket = new.tickets[0].id if new.tickets else ""
    if old_ticket != new_ticket and new_ticket:
        entries.append({"kind": "transport", "leg_id": "leg-out", "id": new_ticket, "to": new.tickets[0].name})
    return entries[:40]
