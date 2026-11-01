"""Minimal-edit path.

The router names the affected parts. Only the agents that own those parts run
again, unlocked stops keep their data, confirmed items are preserved, and the
version increments by one.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.application.planning import PlanPath
from app.application.planner import Planner, _constraints
from app.application.toolbox import Toolbox
from app.application.validation import diff_plans, repair_plan, tidy_data_gaps, validate_plan
from app.config import Settings
from app.domain.errors import CanaryLeak, PlanInvalidError
from app.domain.models import (
    AgentName,
    DataStatus,
    DayPlan,
    GeoLocation,
    HotelStay,
    Money,
    PlanItem,
    PriceKind,
    SourceRef,
    TicketOption,
    TripContext,
    TripPlan,
    WeatherDay,
    utcnow,
)
from app.domain.ports import Tracer
from app.security import fence_user_message

AGENT_SECTION: dict[AgentName, str] = {
    AgentName.ATTRACTION: "attractions",
    AgentName.HOTEL: "hotels",
    AgentName.WEATHER: "weather",
    AgentName.TICKET: "tickets",
}

PART_AGENTS: dict[str, set[AgentName]] = {
    "hotel": {AgentName.HOTEL},
    "budget": {AgentName.HOTEL},
    "attraction": {AgentName.ATTRACTION},
    "ticket": {AgentName.TICKET},
    "weather": {AgentName.WEATHER},
    "dates": {AgentName.WEATHER, AgentName.HOTEL, AgentName.TICKET},
}


class ChangeDraft(BaseModel):
    description: str = ""
    affected_parts: list[str] = Field(default_factory=list)
    target_day: int | None = None
    target_item_id: str | None = None
    new_values: dict[str, Any] = Field(default_factory=dict)
    is_question: bool = False
    confidence: float = 0.0


class ModifyPath:
    def __init__(self, *, planner: Planner, plan_path: PlanPath, settings: Settings, gateway: Any):
        self.planner = planner
        self.plan_path = plan_path
        self.settings = settings
        self.gateway = gateway

    async def run(
        self,
        *,
        plan: TripPlan,
        context: TripContext,
        message: str,
        route_parts: list[str],
        toolbox: Toolbox,
        tracer: Tracer,
        emit,
        memory_summaries: list[str] | None = None,
        scope_item_id: str | None = None,
    ) -> tuple[TripPlan, list[dict[str, Any]]]:
        fx = getattr(self.plan_path.registry, "fx", None)
        if fx is not None:
            try:
                with tracer.span("fx.refresh"):
                    await fx.refresh()
            except Exception:
                pass

        draft = await self._extract(message, plan, context, route_parts)
        if scope_item_id and not draft.target_item_id and plan.find_item(scope_item_id):
            # The console's "About: Day 3 · X" chip is a real context signal.
            # "remove this" / "swap it" must resolve to that stop, and a real
            # imperative with a selected item is a change even if the writer
            # calls the terseness a question.
            draft.target_item_id = scope_item_id
            if re.search(
                r"\b(remove|delete|drop|swap|replace|change|make|add|include|move)\b",
                message.lower(),
            ):
                draft.is_question = False
        if draft.is_question:
            raise PlanInvalidError(
                "This message reads as a question, not a change. Ask it as a question and I will answer "
                "without touching the plan."
            )
        affected = [part for part in draft.affected_parts if part in PART_AGENTS or part.startswith("day_")]
        if route_parts:
            # The router is the authority on scope. The writer may narrow it but
            # must not silently add an agent the router did not name.
            narrowed = [part for part in affected if part in route_parts or part.startswith("day_")]
            affected = narrowed or route_parts
        if not affected:
            affected = ["attraction"]
        agent_names: set[AgentName] = set()
        for part in affected:
            agent_names |= PART_AGENTS.get(part, {AgentName.ATTRACTION})
        budget_mode = "budget" in affected or bool(
            re.search(
                r"\b(cheap|cheaper|optimis|optimiz|reduce|lower|afford|over budget|make it work)\b",
                message.lower(),
            )
        )
        if budget_mode and context.budget is not None:
            # A budget request is not only a hotel request: the flight is often
            # the largest line, so both priced sections are re-run.
            agent_names.add(AgentName.HOTEL)
            agent_names.add(AgentName.TICKET)

        old_plan = plan
        new_range = context.date_range()
        date_requested = "dates" in affected or bool(
            re.search(r"\b(date|dates|day|days|night|nights|extend|shorten|earlier|later|move)\b", message.lower())
        )
        if new_range and new_range != plan.dates and not date_requested:
            # The conversation must not drift: only an explicit date change may
            # move the trip. Restore the plan's dates into the context.
            context.start_date = plan.dates.start
            context.end_date = plan.dates.end
            context.days = len(plan.dates.dates)
            new_range = plan.dates
        date_changed = bool(new_range and new_range != plan.dates)
        if date_changed and len(new_range.dates) != len(plan.days):
            # A different length is a different shape; rebuild with the same id and version + 1.
            rebuilt = await self.plan_path.run(
                context=context,
                session_id=plan.session_id,
                toolbox=toolbox,
                tracer=tracer,
                emit=emit,
                memory_summaries=memory_summaries,
                version=plan.version + 1,
                trip_id=plan.trip_id,
                change_summary=draft.description or "Changed the trip dates; rebuilt the itinerary.",
            )
            _carry_confirmed(plan, rebuilt)
            errors = validate_plan(rebuilt, self.settings)
            if errors:
                rebuilt = await repair_plan(rebuilt, errors, self.gateway, self.settings)
            return rebuilt, diff_plans(old_plan, rebuilt)

        destination = GeoLocation(
            name=plan.destination.name,
            country_code=plan.destination.country_code,
            timezone=plan.destination.timezone,
            geo=plan.destination.geo,
            source="current plan",
        )
        date_range = new_range or plan.dates
        with tracer.span("modify.agents", parts=",".join(sorted(agent_names))):
            results = await self.plan_path.run_agents(
                context=context,
                destination=destination,
                date_range=date_range,
                toolbox=toolbox,
                emit=emit,
                names=agent_names,
                extra={"places_query": _places_query(message)} if AgentName.ATTRACTION in agent_names else None,
            )

        updated = plan.model_copy(deep=True)
        # A section that is rerun gets a fresh gap verdict. Without this, an old
        # Wikimedia/weather/ticket gap survives after the condition is gone.
        refreshed_sections = {AGENT_SECTION[name] for name in agent_names}
        updated.data_quality.gaps = [
            gap for gap in updated.data_quality.gaps if gap.section not in refreshed_sections
        ]
        if date_changed and new_range:
            updated.dates = new_range
            for index, day in enumerate(updated.days):
                day.date = new_range.dates[index]
            if updated.hotels:
                updated.hotels[0].check_in = new_range.start
                updated.hotels[0].check_out = new_range.end
            updated.tickets = [ticket for ticket in updated.tickets if _ticket_within(ticket, new_range)]

        if AgentName.HOTEL in agent_names:
            hotels = results.get("hotel")
            candidates = (hotels.payload.get("hotels") if hotels else []) or []
            current = updated.hotels[0].name if updated.hotels else ""
            current_price = (
                updated.hotels[0].price_per_night.amount
                if updated.hotels and updated.hotels[0].price_per_night
                else None
            )
            cheaper = budget_mode or bool(re.search(r"\b(cheap|cheaper|budget|lower|reduce|less)\b", message.lower()))
            pool = [row for row in candidates if row.get("name") != current]
            if cheaper:
                pool.sort(key=lambda row: _hotel_price(row) if _hotel_price(row) is not None else float("inf"))
                if current_price is not None:
                    pool = [row for row in pool if (_hotel_price(row) or current_price) < current_price]
            choice = pool[0] if pool else None
            if choice:
                updated.hotels = [self.planner._build_hotel(choice, updated.dates)]  # type: ignore[list-item]
            else:
                updated.data_quality.gaps.append(
                    _gap("hotels", "No cheaper verified hotel was returned; the current hotel stays.")
                )

        if AgentName.WEATHER in agent_names:
            weather_result = results.get("weather")
            rows = (weather_result.payload.get("days") if weather_result else []) or []
            weather_by_date = {row.get("date"): row for row in rows}
            for day in updated.days:
                row = weather_by_date.get(day.date.isoformat())
                if row:
                    day.weather = WeatherDay.model_validate(row)

        if AgentName.ATTRACTION in agent_names:
            attraction_result = results.get("attraction")
            places = (attraction_result.payload.get("places") if attraction_result else []) or []
            lower_message = message.lower()
            add_requested = bool(re.search(r"\b(add|include|insert|append|one more|another|also)\b", lower_message))
            remove_requested = bool(re.search(r"\b(remove|delete|drop|take out|without)\b", lower_message))
            if add_requested:
                added = _add_attractions(updated, places, message)
                if not added:
                    wanted = [
                        word for word in _ADD_KEYWORDS if re.search(rf"\b{word}s?\b", message.lower())
                    ]
                    detail = f"a verified {wanted[0]}" if wanted else "a verified stop"
                    updated.data_quality.gaps.append(
                        _gap(
                            "attractions",
                            f"I could not find {detail} from the configured sources, or the day had no free time slot; the itinerary stayed as it was.",
                        )
                    )
            elif remove_requested:
                removed = _remove_attraction(updated, draft, message)
                if not removed:
                    updated.data_quality.gaps.append(
                        _gap(
                            "attractions",
                            "No unlocked stop matched that removal, so the itinerary stayed as it was.",
                        )
                    )
            else:
                target = _target_item(updated, draft, message)
                replaced = _replace_attractions(updated, places, target, draft.target_day)
                if not replaced:
                    detail = f"a verified replacement for {target!r}" if target else "a verified alternative stop"
                    updated.data_quality.gaps.append(
                        _gap(
                            "attractions",
                            f"I could not find {detail} from the configured sources; the current stops were kept.",
                        )
                    )

        if AgentName.TICKET in agent_names:
            ticket_result = results.get("ticket")
            flights = (ticket_result.payload.get("flights") if ticket_result else []) or []
            if flights:
                built = self.planner._build_tickets(flights)
                if budget_mode:
                    built = _cheapest_first(built, updated.tickets)
                if built:
                    updated.tickets = built

        # Budget and cost are always recalculated after a change.
        if context.budget is not None:
            updated.budget = Money(amount=context.budget, currency=context.currency)
        updated.cost = self.planner._cost(
            context,
            updated.hotels[0] if updated.hotels else None,
            updated.tickets,
            updated.dates,
        )
        if budget_mode and updated.cost.status == "over":
            budget = updated.cost.budget or 0.0
            over = updated.cost.total - budget
            biggest = max(updated.cost.lines, key=lambda line: line.amount, default=None)
            detail = (
                f" The largest line is {biggest.label} at "
                f"{updated.cost.currency} {biggest.amount:,.0f}."
                if biggest
                else ""
            )
            updated.data_quality.gaps.append(
                _gap(
                    "budget",
                    f"The cheapest verified combination is {updated.cost.currency} "
                    f"{updated.cost.total:,.0f}, which is {over:,.0f} over the "
                    f"{updated.cost.currency} {budget:,.0f} budget.{detail} "
                    "A shorter stay, different dates, or a higher budget is needed.",
                )
            )
        updated.constraints = _constraints(
            context,
            updated.days,
            updated.hotels[0] if updated.hotels else None,
            updated.tickets,
            updated.cost,
        )
        updated.version = plan.version + 1
        updated.updated_at = utcnow()
        updated.change_summary = draft.description or "Applied a local change."
        updated.data_quality.notes = _merge_notes(updated.data_quality.notes, draft.description)

        errors = validate_plan(updated, self.settings)
        if errors:
            emit("progress", {"phase": "repair", "message": "Repairing the edited itinerary.", "progress": 85})
            updated = await repair_plan(updated, errors, self.gateway, self.settings)
        updated = tidy_data_gaps(updated)
        return updated, diff_plans(old_plan, updated)

    async def _extract(
        self,
        message: str,
        plan: TripPlan,
        context: TripContext,
        route_parts: list[str],
    ) -> ChangeDraft:
        if self.gateway.available:
            user = (
                f"Current plan summary: {json.dumps(plan.summary_for_prompt())}\n"
                f"Trip context: {context.model_dump_json()}\n\n"
                + fence_user_message(message)
            )
            try:
                data = await self.gateway.json("modify", user=user, purpose="modify.extract")
                draft = ChangeDraft.model_validate(data)
                if not draft.affected_parts:
                    draft.affected_parts = route_parts
                return draft
            except CanaryLeak:
                raise
            except Exception:
                pass
        return ChangeDraft(
            description=message.strip()[:180],
            affected_parts=route_parts or _rule_parts(message),
            target_day=_rule_day(message),
            confidence=0.5,
        )


def _rule_parts(message: str) -> list[str]:
    lower = message.lower()
    parts: list[str] = []
    for name, pattern in [
        ("hotel", r"\b(hotel|stay|room|hostel|ryokan)\b"),
        ("attraction", r"\b(attraction|museum|temple|shrine|sight|stop|visit)\b"),
        ("weather", r"\b(weather|rain|forecast|typhoon)\b"),
        ("ticket", r"\b(flight|train|ticket|fare)\b"),
        ("budget", r"\b(budget|cheaper|expensive|cost)\b"),
        ("dates", r"\b(date|dates|day|nights|extend|shorten)\b"),
    ]:
        if re.search(pattern, lower):
            parts.append(name)
    day = _rule_day(message)
    if day:
        parts.append(f"day_{day}")
    return sorted(set(parts))


def _rule_day(message: str) -> int | None:
    match = re.search(r"\bday\s*(\d{1,2})\b|\b(\d{1,2})(?:st|nd|rd|th)\s+day\b", message.lower())
    if match:
        return int(match.group(1) or match.group(2))
    ordinals = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
    for word, index in ordinals.items():
        if re.search(rf"\b{word}\s+day\b", message.lower()):
            return index
    return None


def _target_item(plan: TripPlan, draft: ChangeDraft, message: str) -> str | None:
    if draft.target_item_id:
        found = plan.find_item(draft.target_item_id)
        if found:
            return found[1].name
    quoted = re.search(r"[\"“']([^\"”']{3,60})[\"”']", message)
    if quoted:
        return quoted.group(1)
    match = re.search(r"\b(?:swap|replace|change|remove)\s+(.+?)\s+(?:for|to|with)\b", message, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def _replace_attractions(plan: TripPlan, places: list[dict], target: str | None, target_day: int | None) -> bool:
    existing_names = {item.name for day in plan.days for item in day.items if item.type.value == "attraction"}
    pool = [row for row in places if row.get("name") not in existing_names]
    if not pool:
        return False
    replaced = False
    used: set[str] = set()
    for day in plan.days:
        if target_day and day.date.day != target_day and (day.date - plan.dates.start).days + 1 != target_day:
            continue
        for item in day.items:
            if item.type.value != "attraction" or item.locked or item.confirmed:
                continue
            if target and target.lower() not in item.name.lower():
                continue
            candidate = next((row for row in pool if row["name"] not in used), None)
            if not candidate:
                break
            used.add(candidate["name"])
            item.name = candidate["name"]
            item.geo = candidate.get("geo") or item.geo
            item.address = candidate.get("address", "")
            item.notes = (candidate.get("description") or "")[:700]
            item.source_refs = [SourceRef.model_validate(ref) for ref in candidate.get("source_refs", [])]
            item.data_status = DataStatus.OK
            item.fetched_at = utcnow()
            item.tags = (candidate.get("tags") or [])[:4]
            replaced = True
    return replaced


def _places_query(message: str) -> str | None:
    wanted = [word for word in _ADD_KEYWORDS if re.search(rf"\b{word}s?\b", message.lower())]
    return " ".join(f"{word}s" for word in wanted) if wanted else None


def _hotel_price(row: dict) -> float | None:
    price = row.get("nightly_price") or {}
    try:
        return float(price.get("amount"))
    except (TypeError, ValueError):
        return None


_ADD_KEYWORDS = (
    "museum",
    "gallery",
    "temple",
    "shrine",
    "park",
    "garden",
    "market",
    "castle",
    "tower",
    "viewpoint",
    "beach",
    "hike",
)

_SLOTS = (("09:00", "10:30"), ("10:30", "12:00"), ("14:00", "15:30"), ("15:30", "17:00"), ("17:00", "18:30"))


def _cheapest_first(tickets: list[TicketOption], current: list[TicketOption]) -> list[TicketOption]:
    """Budget mode: keep the round-trip fare that costs least."""
    priced = [ticket for ticket in tickets if ticket.price]
    if not priced:
        return current
    cheapest = min(priced, key=lambda ticket: ticket.price.amount)  # type: ignore[union-attr]
    previous = current[0] if current else None
    if (
        previous
        and previous.price
        and cheapest.price
        and cheapest.price.amount > previous.price.amount
    ):
        return current
    return [cheapest] + [ticket for ticket in tickets if ticket.id != cheapest.id][:2]


def _free_slot(day: DayPlan) -> tuple[str, str] | None:
    for start, end in _SLOTS:
        if all(end <= item.start or start >= item.end for item in day.items):
            return start, end
    return None


def _late_available(day: DayPlan) -> bool:
    dinner = next(
        (item for item in day.items if item.type.value == "meal" and item.start >= "18:00"),
        None,
    )
    if dinner is None:
        return False
    start, end = "18:30", "19:30"
    return all(end <= item.start or start >= item.end for item in day.items if item is not dinner)


def _late_slot(day: DayPlan) -> tuple[str, str] | None:
    """Use the early evening by moving dinner, instead of refusing a full day."""
    if not _late_available(day):
        return None
    dinner = next(
        item for item in day.items if item.type.value == "meal" and item.start >= "18:00"
    )
    dinner.start = "19:45"
    dinner.end = "21:00"
    return "18:30", "19:30"


def _add_attractions(plan: TripPlan, places: list[dict], message: str) -> list[str]:
    """Add verified candidates instead of renaming every existing stop."""
    existing = {item.name for day in plan.days for item in day.items if item.type.value == "attraction"}
    wanted = [word for word in _ADD_KEYWORDS if re.search(rf"\b{word}s?\b", message.lower())]
    pool = []
    for row in places:
        if not row.get("name") or row.get("name") in existing or not row.get("source_refs"):
            continue
        text = f"{row.get('name', '')} {row.get('description', '')}".lower()
        if wanted and not any(word in text for word in wanted):
            continue
        pool.append(row)
    if not pool:
        return []

    count = 2 if re.search(
        r"\b(?:two|2)\s+(?:more\s+)?(?:museums?|attractions?|stops?|places?)\b|\ba couple\b",
        message.lower(),
    ) else 1
    day_number = _rule_day(message)
    preferred = plan.days[day_number - 1] if day_number and 1 <= day_number <= len(plan.days) else None
    added: list[str] = []
    for row in pool[:count]:
        day = preferred
        if day is None:
            day = min(
                plan.days,
                key=lambda entry: sum(1 for item in entry.items if item.type.value == "attraction"),
            )
        slot = _free_slot(day) or _late_slot(day)
        if slot is None:
            # A targeted day is full; try another day only when the message did
            # not name a day explicitly.
            if preferred is not None:
                break
            day = next(
                (entry for entry in plan.days if _free_slot(entry) is not None or _late_available(entry)),
                None,
            )
            if day is None:
                break
            slot = _free_slot(day) or _late_slot(day)
        start, end = slot
        item_id = f"item_{row.get('id', len(added))}"
        existing_ids = {item.id for day_plan in plan.days for item in day_plan.items}
        if item_id in existing_ids:
            item_id = f"item_added_{len(added)}_{row.get('id', 'x')}"
        item = PlanItem(
            id=item_id,
            type="attraction",
            name=row["name"],
            start=start,
            end=end,
            geo=row.get("geo"),
            address=row.get("address", ""),
            notes=(row.get("description") or "Added from a verified source.")[:700],
            source_refs=[SourceRef.model_validate(ref) for ref in row.get("source_refs", [])],
            data_status=DataStatus.OK,
            fetched_at=utcnow(),
            tags=(row.get("tags") or [])[:4],
        )
        day.items.append(item)
        day.items.sort(key=lambda entry: entry.start)
        added.append(item.name)
    return added


def _remove_attraction(plan: TripPlan, draft: ChangeDraft, message: str) -> str | None:
    target = _target_item(plan, draft, message)
    wanted = [word for word in _ADD_KEYWORDS if re.search(rf"\b{word}s?\b", message.lower())]
    for day in plan.days:
        for item in day.items:
            # A selected stop can be a meal as well as an attraction; the scope
            # chip is about the row the traveller tapped.
            if item.locked or item.confirmed:
                continue
            if target and target.lower() not in item.name.lower():
                continue
            if not target and wanted and not any(word in item.name.lower() for word in wanted):
                continue
            day.items.remove(item)
            return item.name
    return None


def _carry_confirmed(old: TripPlan, new: TripPlan) -> None:
    names = {item.name for day in old.days for item in day.items if item.confirmed or item.locked}
    if not names:
        return
    for day in new.days:
        for item in day.items:
            if item.name in names:
                item.confirmed = True


def _ticket_within(ticket: TicketOption, date_range) -> bool:
    if not ticket.depart_at:
        return True
    return date_range.start <= ticket.depart_at.date() <= date_range.end


def _gap(section: str, message: str):
    from app.domain.models import DataGap, FailureKind

    return DataGap(section=section, kind=FailureKind.NO_DATA, message=message, retryable=False)


def _merge_notes(notes: list[str], description: str) -> list[str]:
    if description and description not in notes:
        return (notes + [description])[:12]
    return notes
