"""Minimal-edit path.

The router names the affected parts. Only the agents that own those parts run
again, unlocked stops keep their data, confirmed items are preserved, and the
version increments by one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from app.application.language import cuisine_slot
from app.application.opening_hours import lines_for
from app.application.planning import PlanPath
from app.application.planner import PACE_BUFFER_MINUTES, Planner, _constraints, _pace
from app.application.prompt_context import plan_outline, trip_slots
from app.application.toolbox import Toolbox
from app.application.transfers import refresh_transfers
from app.application.trip_rules import blocked_dates, travel_windows
from app.application.validation import diff_plans, repair_plan, tidy_data_gaps, validate_plan
from app.config import Settings
from app.domain.errors import CanaryLeak, PlanInvalidError
from app.domain.models import (
    AgentName,
    DataStatus,
    DayPlan,
    Disruption,
    GeoLocation,
    GeoPoint,
    HotelStay,
    ItemType,
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
from app.domain.place_names import trusted_names
from app.domain.ports import Tracer
from app.infrastructure.http import haversine_km
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
        draft = await self._extract(message, plan, context, route_parts)
        if scope_item_id and not draft.target_item_id and plan.find_item(scope_item_id):
            # The console's "About: Day 3 · X" chip is a real context signal.
            # "remove this" / "swap it" must resolve to that stop, and a real
            # imperative with a selected item is a change even if the writer
            # calls the terseness a question.
            draft.target_item_id = scope_item_id
            if re.search(
                r"\b(remove|delete|drop|swap|replace|change|switch|make|add|include|move|eat|dinner|lunch|restaurant)\b",
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
        if re.search(r"\b(hours?|minutes?)\b", message.lower()) and not re.search(
            r"\b(cheap|cheaper|money|price|budget|hkd|usd|\$)\b", message.lower()
        ):
            affected = [part for part in affected if part != "budget"]
        # "day 2", "the last two nights" or "a later flight" name a part of the
        # trip, not new dates. Weather, hotel and flights rerun for "dates" only
        # when the trip's dates actually move (Proposal DB-09).
        dates_move = bool(context.date_range() and context.date_range() != plan.dates)
        agent_names: set[AgentName] = set()
        for part in affected:
            if part == "dates" and not dates_move:
                continue
            agent_names |= PART_AGENTS.get(part, {AgentName.ATTRACTION})
        if not agent_names:
            agent_names = {AgentName.ATTRACTION}
        budget_mode = "budget" in affected or bool(
            re.search(
                r"\b(cheap|cheaper|optimis|optimiz|reduce|lower|afford|over budget|make it work)\b",
                message.lower(),
            )
        )
        agent_names = lock_scope(
            agent_names,
            plan,
            message,
            scope_item_id,
        )
        referred = resolve_referred(plan, message, scope_item_id)
        shape = interpret_plan(message, draft.new_values)
        # A pace or meal rule is about the whole plan. A selected chip must
        # not turn it into a question about one stop.
        if shape.requested and _name_mentioned(plan, message) is None:
            referred = None
            draft.is_question = False
            agent_names = set()
        if referred:
            # The traveller pointed at one row. No other row, hotel, or flight
            # may change, even if the router named a whole section.
            draft.target_item_id = referred
            draft.is_question = False
            agent_names = (
                {AgentName.HOTEL} if plan.find_hotel(referred) else {AgentName.ATTRACTION}
            )
        if budget_mode and context.budget is not None and not referred:
            # A budget request is not only a hotel request: the flight is often
            # the largest line, so both priced sections are re-run.
            agent_names.add(AgentName.HOTEL)
            agent_names.add(AgentName.TICKET)

        if budget_mode and not _has_prices(plan):
            # Nothing in the plan carries a price, so "make it cheaper" has
            # nothing to act on. Ask instead of re-running agents and answering
            # "no cheaper hotel was returned", which reads like a failure.
            scoped = _scoped_name(plan, scope_item_id)
            unchanged = plan.model_copy(deep=True)
            unchanged.change_summary = (
                "Nothing in this plan has a price yet — the hotel and flight sources "
                "returned no rates, and "
                + (f"{scoped} carries no price of its own. " if scoped else "no stop carries a price. ")
                + "Do you want me to replace "
                + (f"{scoped}" if scoped else "the selected stop")
                + " with another verified option, or keep the itinerary as it is?"
            )
            return unchanged, []

        old_plan = plan
        new_range = context.date_range()
        date_requested = not shape.requested and (
            "dates" in affected or bool(
                re.search(r"\b(date|dates|day|days|night|nights|extend|shorten|earlier|later|move)\b", message.lower())
            )
        )
        if new_range and new_range != plan.dates and not date_requested:
            # The conversation must not drift: only an explicit date change may
            # move the trip. Restore the plan's dates into the context.
            context.start_date = plan.dates.start
            context.end_date = plan.dates.end
            context.days = len(plan.dates.dates)
            new_range = plan.dates
        date_changed = bool(new_range and new_range != plan.dates) and not referred
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
        if date_changed:
            # The trip moved: its forecast, stay and flights are for other days now.
            agent_names |= PART_AGENTS["dates"]
        date_range = new_range or plan.dates
        lower_message = message.lower()
        add_requested = _ADD_REQUEST.search(lower_message) is not None
        remove_requested = _REMOVE_REQUEST.search(lower_message) is not None
        # The agents whose data this edit needs (Proposal DB-09). A pace change
        # works from the plan alone, and taking a stop out needs no new places.
        needed = set(agent_names)
        if shape.requested and referred is None:
            needed = set()
        elif remove_requested and not add_requested:
            needed.discard(AgentName.ATTRACTION)
        fx = getattr(self.plan_path.registry, "fx", None)
        if fx is not None and (budget_mode or needed & {AgentName.HOTEL, AgentName.TICKET}):
            # Rates are needed only when a priced line (stay or flight) can change.
            try:
                with tracer.span("fx.refresh"):
                    await toolbox.call("fx.rates", fx.refresh)
            except Exception:
                pass
        if not needed:
            # run_agents treats an empty selection as "all agents".
            results = {}
        else:
            with tracer.span("modify.agents", parts=",".join(sorted(needed))):
                results = await self.plan_path.run_agents(
                    context=context,
                    destination=destination,
                    date_range=date_range,
                    toolbox=toolbox,
                    emit=emit,
                    names=needed,
                    extra=_search_extra(message) if AgentName.ATTRACTION in needed else None,
                )

        updated = plan.model_copy(deep=True)
        # A section that is rerun gets a fresh gap verdict. Without this, an old
        # Wikimedia/weather/ticket gap survives after the condition is gone.
        refreshed_sections = {AGENT_SECTION[name] for name in needed}
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

        # Only an edit that names the hotel or the budget may change which hotel it is. When the
        # dates move, the stay the traveller accepted is priced again for the new dates, not swapped.
        hotel_requested = (
            bool({"hotel", "budget"} & set(affected))
            or budget_mode
            or (referred is not None and plan.find_hotel(referred) is not None)
        )
        if AgentName.HOTEL in agent_names and (referred is None or plan.find_hotel(referred)):
            hotels = results.get("hotel")
            candidates = (hotels.payload.get("hotels") if hotels else []) or []
            current = updated.hotels[0].name if updated.hotels else ""
            current_price = (
                updated.hotels[0].price_per_night.amount
                if updated.hotels and updated.hotels[0].price_per_night
                else None
            )
            if updated.hotels and not hotel_requested:
                same = next((row for row in candidates if row.get("name") == current), None)
                rebuilt = self.planner._build_hotel(same, updated.dates) if same is not None else None
                if rebuilt is not None:
                    updated.hotels = [rebuilt]
                else:
                    updated.data_quality.gaps.append(
                        _gap("hotels", "The hotel's price for the new dates could not be refreshed.")
                    )
            else:
                cheaper = budget_mode or bool(
                    re.search(r"\b(cheap|cheaper|budget|lower|reduce|less)\b", message.lower())
                )
                pool = [row for row in candidates if row.get("name") != current and _hotel_near(updated, row)]
                if cheaper:
                    pool.sort(key=lambda row: _hotel_price(row) if _hotel_price(row) is not None else float("inf"))
                    if current_price is not None:
                        pool = [row for row in pool if (_hotel_price(row) or current_price) < current_price]
                choice = pool[0] if pool else None
                swapped = self.planner._build_hotel(choice, updated.dates) if choice else None
                if swapped is not None:
                    updated.hotels = [swapped]
                else:
                    updated.data_quality.gaps.append(
                        _gap("hotels", "No cheaper verified hotel was returned; the current hotel stays.")
                    )

        if AgentName.WEATHER in agent_names and referred is None:
            weather_result = results.get("weather")
            rows = (weather_result.payload.get("days") if weather_result else []) or []
            weather_by_date = {row.get("date"): row for row in rows}
            for day in updated.days:
                row = weather_by_date.get(day.date.isoformat())
                if row:
                    day.weather = WeatherDay.model_validate(row)

        if shape.requested and referred is None:
            draft.description = shape.reason or apply_plan_shape(updated, shape)
        elif AgentName.ATTRACTION in agent_names:
            attraction_result = results.get("attraction")
            payload = attraction_result.payload if attraction_result else {}
            places = payload.get("places") or []
            meals = payload.get("meals") or []
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
                resolved = _resolve_target(updated, draft, message)
                if resolved is None:
                    updated.data_quality.gaps.append(
                        _gap(
                            "attractions",
                            "I could not tell which stop you meant. Name it or give its number in the day"
                            f"{_stop_example(updated)}, or select it first; the itinerary stayed as it was.",
                        )
                    )
                else:
                    day, target_item = resolved
                    before = target_item.name
                    existing = _requested_alternative(updated, message)
                    if existing is not None and existing[1].id != target_item.id:
                        # "change Ocean Park to Disneyland" when Disneyland is
                        # already on another slot: the plan may not hold a place
                        # twice, so explain instead of "no changes needed".
                        day_index = updated.days.index(existing[0]) + 1
                        wanted = existing[1]
                        updated.data_quality.gaps.append(
                            _gap(
                                "attractions",
                                f"{wanted.name} is already in the plan on Day {day_index} at "
                                f"{wanted.start}, so it cannot be added twice. Name a different "
                                f"replacement, or ask me to remove the existing one first.",
                            )
                        )
                    else:
                        wanted_text = _requested_alternative_text(message)
                        cuisine = _cuisine(message)
                        replaced = _replace_target(
                            updated,
                            places,
                            meals,
                            (day, target_item),
                            message,
                            distance_aware=_FAR_MOTION.search(message) is not None,
                            prefer_name=wanted_text,
                            cuisine=cuisine,
                        )
                        closure = _CLOSURE.search(message) is not None
                        if closure:
                            # A reported closure is a disruption on the plan's record,
                            # whether or not a replacement was found.
                            updated.disruptions.append(
                                Disruption(
                                    kind="closure",
                                    summary=f"{before} was reported closed.",
                                    affected_item_ids=[target_item.id],
                                    resolution=(
                                        f"Replaced with {target_item.name}." if replaced
                                        else "No verified alternative came back; the stop is still in the plan."
                                    ),
                                    resolved=replaced,
                                )
                            )
                        if replaced:
                            draft.description = f"Replaced {before} with {target_item.name}."
                        else:
                            detail = (
                                f"No verified {cuisine} restaurant came back, so {before} stayed as it was"
                                if cuisine
                                else (
                                    f"I could not find a verified {wanted_text!r} in the configured sources"
                                    if wanted_text
                                    else f"No verified alternative for {before!r} came back from the configured sources"
                                )
                            )
                            updated.data_quality.gaps.append(
                                _gap(
                                    "attractions",
                                    f"{detail}, so that stop stayed as it was.",
                                )
                            )

        if AgentName.TICKET in agent_names and referred is None:
            ticket_result = results.get("ticket")
            flights = (ticket_result.payload.get("flights") if ticket_result else []) or []
            if flights:
                built = self.planner._build_tickets(flights)
                if budget_mode:
                    built = _cheapest_first(built, updated.tickets)
                if built:
                    updated.tickets = built

        if referred:
            keep_only(old_plan, updated, referred)
            draft.description = _one_item_summary(old_plan, updated, referred)
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
        rule_texts = [*context.hard_constraints, *context.preferences]
        plan_dates = [day.date for day in updated.days]
        updated.constraints = _constraints(
            context,
            updated.days,
            updated.hotels[0] if updated.hotels else None,
            updated.tickets,
            updated.cost,
            blocked=blocked_dates(rule_texts, plan_dates),
            windows=travel_windows(rule_texts, plan_dates),
        )
        updated.updated_at = utcnow()
        updated.change_summary = draft.description or "Applied a local change."
        updated.data_quality.notes = _merge_notes(updated.data_quality.notes, draft.description)
        # A changed day gets transfers that match its new neighbours, from a logged
        # routes.segments call, and no stop starts before its transfer is done
        # (Proposal TC-07 and TC-09).
        await refresh_transfers(
            old_plan,
            updated,
            toolbox=toolbox,
            source=getattr(self.plan_path.registry, "transfers", None),
            buffer=PACE_BUFFER_MINUTES.get(_pace(context), 5),
        )

        errors = validate_plan(updated, self.settings)
        if errors:
            emit("progress", {"phase": "repair", "message": "Repairing the edited itinerary.", "progress": 85})
            updated = await repair_plan(updated, errors, self.gateway, self.settings)
        updated = tidy_data_gaps(updated)

        diff = diff_plans(old_plan, updated)
        if diff:
            updated.version = plan.version + 1
        else:
            # Nothing actually moved. Keeping the version and naming the reason is
            # better than shipping "v2" and telling the traveller nothing changed.
            updated.version = plan.version
            updated.change_summary = (
                updated.data_quality.gaps[-1].message
                if updated.data_quality.gaps
                else "Nothing in the plan matched that request, so it is unchanged."
            )
        return updated, diff

    async def _extract(
        self,
        message: str,
        plan: TripPlan,
        context: TripContext,
        route_parts: list[str],
    ) -> ChangeDraft:
        if self.gateway.available:
            # Proposal TC-10: the item ids, days and times an edit can target, and
            # the trip slots, not the stored context or the plan's status fields.
            user = (
                f"Current plan:\n{plan_outline(plan.summary_for_prompt(), ids=True)}\n"
                f"Trip: {trip_slots(context, preferences=True)}\n\n"
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


#: wording that asks for a *closer* stop rather than a like-for-like swap
_FAR_MOTION = re.compile(
    r"\b(too far|far away|farther|further away|closer|nearer|near by|nearby|walking distance)\b",
    re.IGNORECASE,
)

#: "change A to B" / "swap A for B" — B is what the traveller wants instead
_ALTERNATIVE_TEXT = re.compile(
    r"\b(?:to|into|with|for)\s+([a-z0-9][a-z0-9 &'\-]{2,60})\s*[.?!]*$",
    re.IGNORECASE,
)


def _norm(text: str) -> str:
    """Lowercase and drop everything that is not a letter or digit.

    Lets "oceanpark" resolve to "Ocean Park" and "HK Disneyland" to
    "Hong Kong Disneyland", which people type all the time.
    """
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _requested_alternative_text(message: str) -> str | None:
    """The place the traveller asked for, taken from the last "to/into/with/for".

    "i want to change oceanpark ... to disneyland" must yield "disneyland", not
    everything after the first "to".
    """
    cleaned = message.strip().rstrip(" .?!")
    lowered = cleaned.lower()
    best: tuple[int, int] | None = None
    for marker in (" to ", " into ", " with ", " for "):
        index = lowered.rfind(marker)
        if index != -1 and (best is None or index > best[0]):
            best = (index, len(marker))
    if best is None:
        return None
    phrase = cleaned[best[0] + best[1] :].strip(" .,?!")
    if not phrase:
        return None
    # "something quieter", "anything else" are preferences, not place names.
    if phrase.lower().startswith(
        ("some", "any", "else", "other", "another", "different", "cheap", "better", "nice", "quiet")
    ):
        return None
    return phrase


def _requested_alternative(plan: TripPlan, message: str) -> tuple[DayPlan, PlanItem] | None:
    """The stop the traveller asked to swap *in*, if it is already in the plan."""
    phrase = _requested_alternative_text(message)
    if not phrase:
        return None
    needle = _norm(phrase)
    if not needle:
        return None
    best: tuple[DayPlan, PlanItem] | None = None
    for day, item in _items_in_order(plan):
        name = _norm(item.name)
        if name and (name in needle or needle in name):
            if best is None or len(name) > len(_norm(best[1].name)):
                best = (day, item)
    return best


def _has_prices(plan: TripPlan) -> bool:
    """True when at least one line of the plan carries a real price."""
    if plan.cost is not None and plan.cost.lines:
        return True
    if plan.hotels and plan.hotels[0].price_per_night is not None:
        return True
    return any(ticket.price is not None for ticket in plan.tickets)


def _scoped_name(plan: TripPlan, scope_item_id: str | None) -> str:
    if not scope_item_id:
        return ""
    found = plan.find_item(scope_item_id)
    return found[1].name if found else ""


def _items_in_order(plan: TripPlan):
    for day in plan.days:
        for item in day.items:
            yield day, item


def _stop_example(plan: TripPlan) -> str:
    """' (for example "2. <a real stop>")' from the current plan, or '' when it has no stops."""
    for day in plan.days:
        for index, item in enumerate(day.items, start=1):
            if item.name.strip():
                return f' (for example "{index}. {item.name.strip()}")'
    return ""


def _resolve_target(plan: TripPlan, draft: ChangeDraft, message: str) -> tuple[DayPlan, PlanItem] | None:
    """Find the stop the traveller pointed at.

    Accepts, in order: the console's selected item, a leading index ("3." / "#3" /
    "item 3"), a quoted name, a name mentioned anywhere in the sentence, or a
    fuzzy token match. Before this, only a quoted name or "swap X for Y" resolved,
    so "3. Ho Lee Fook too far away" pointed at nothing.
    """
    if draft.target_item_id:
        found = plan.find_item(draft.target_item_id)
        if found:
            return found[0], found[1]

    lowered = message.lower()
    match = re.match(r"\s*#?(\d{1,2})\s*[.)\]:\-\s]", lowered) or re.search(
        r"\b(?:item|stop|number|no\.?)\s*#?(\d{1,2})\b", lowered
    )
    if match:
        index = int(match.group(1))
        for day_index, day in enumerate(plan.days, start=1):
            if draft.target_day and draft.target_day != day_index:
                continue
            if 1 <= index <= len(day.items):
                return day, day.items[index - 1]

    quoted = re.search(r"[\"“']([^\"”']{3,60})[\"”']", message)
    if quoted:
        needle = quoted.group(1).lower()
        for day, item in _items_in_order(plan):
            if needle in item.name.lower():
                return day, item

    # the longest plan item whose name appears verbatim in the sentence
    best: tuple[DayPlan, PlanItem] | None = None
    for day, item in _items_in_order(plan):
        name = item.name.lower()
        if name and name in lowered:
            if best is None or len(name) > len(best[1].name):
                best = (day, item)
    if best is not None:
        return best

    # ignore spacing and punctuation: "oceanpark" must resolve to "Ocean Park"
    squashed = _norm(message)
    squashed_best: tuple[DayPlan, PlanItem] | None = None
    for day, item in _items_in_order(plan):
        name = _norm(item.name)
        if name and name in squashed:
            if squashed_best is None or len(name) > len(_norm(squashed_best[1].name)):
                squashed_best = (day, item)
    if squashed_best is not None:
        return squashed_best

    # token overlap, so a truncated or re-ordered name still resolves: either two
    # shared words, or one distinctive word of four or more characters
    words = {w for w in re.findall(r"[a-z0-9]{3,}", lowered)}
    scored: list[tuple[int, DayPlan, PlanItem]] = []
    for day, item in _items_in_order(plan):
        item_words = {w for w in re.findall(r"[a-z0-9]{3,}", item.name.lower())}
        shared = words & item_words
        if len(shared) >= 2 or any(len(word) >= 4 for word in shared):
            scored.append((len(shared), day, item))
    if scored:
        scored.sort(key=lambda row: -row[0])
        return scored[0][1], scored[0][2]
    return None


def _anchor_geo(plan: TripPlan, day: DayPlan, target: PlanItem):
    """Where "too far away" is measured from: the hotel, else the previous stop."""
    if plan.hotels and plan.hotels[0].geo:
        return plan.hotels[0].geo
    previous = None
    for item in day.items:
        if item is target:
            break
        if item.geo:
            previous = item.geo
    if previous is not None:
        return previous
    for item in day.items:
        if item is not target and item.geo:
            return item.geo
    return plan.destination.geo


def _candidate_pool(item: PlanItem, places: list[dict], meals: list[dict], used: set[str]) -> list[dict]:
    """Meals are replaced from the meal pool, everything else from the place pool."""
    source = meals if item.type is ItemType.MEAL else places
    return [row for row in source if row.get("name") and row["name"] not in used]


_CLOSURE = re.compile(r"\b(closed|closure|shut|suspended|not operating|cancel+ed)\b", re.IGNORECASE)
_ADD_REQUEST = re.compile(r"\b(add|include|insert|append|one more|another|also)\b")
_REMOVE_REQUEST = re.compile(r"\b(remove|delete|drop|take out|without)\b")


def _stated_hours(row: dict, day_date: date | None) -> tuple[list[str], str]:
    """The new place's own hours lines for the day, or "unknown" when its source gave none."""
    stated = [str(line) for line in (row.get("opening_hours") or []) if str(line).strip()]
    lines = lines_for(stated, day_date) if day_date else stated[:7]
    return lines, ("stated" if lines else "unknown")


def _apply_candidate(item: PlanItem, row: dict, day_date: date | None = None) -> None:
    item.opening_hours, item.hours_status = _stated_hours(row, day_date)  # type: ignore[assignment]
    item.name = row["name"]
    # Assignment is not validated, so a raw dict would stay a dict, not a GeoPoint.
    item.geo = GeoPoint.model_validate(row["geo"]) if row.get("geo") else item.geo
    item.address = row.get("address", "")
    item.notes = (row.get("description") or "")[:700]
    refs = row.get("source_refs") or []
    if refs:
        item.source_refs = [SourceRef.model_validate(ref) for ref in refs]
    item.data_status = DataStatus.OK
    item.fetched_at = utcnow()
    item.tags = (row.get("tags") or [])[:4]


def _replace_target(
    plan: TripPlan,
    places: list[dict],
    meals: list[dict],
    target: tuple[DayPlan, PlanItem],
    message: str,
    *,
    distance_aware: bool,
    prefer_name: str | None = None,
    cuisine: str | None = None,
) -> bool:
    day, item = target
    if item.locked or item.confirmed:
        return False
    used = {other.name for _, other in _items_in_order(plan)}
    pool = _candidate_pool(item, places, meals, used)
    if prefer_name:
        needle = _norm(prefer_name)
        preferred = [
            row
            for row in pool
            if needle and (needle in _norm(row["name"]) or _norm(row["name"]) in needle)
        ]
        if preferred:
            pool = preferred
    if not pool:
        return False
    anchor_geo = item.geo or plan.destination.geo
    if anchor_geo is not None:
        local = [row for row in pool if _row_near(anchor_geo, row, 30)]
        if not local:
            return False
        pool = local
    if cuisine:
        hints = _CUISINE_HINTS.get(cuisine, (cuisine,))
        matched = [
            row
            for row in pool
            if any(hint in f"{row.get('name', '')} {row.get('description', '')}".lower() for hint in hints)
        ]
        if not matched:
            return False
        pool = matched
    if distance_aware:
        anchor = _anchor_geo(plan, day, item)
        if anchor is not None:

            def distance(row: dict) -> float:
                geo = row.get("geo") or {}
                lat, lng = geo.get("lat"), geo.get("lng")
                if lat is None or lng is None:
                    return float("inf")
                return haversine_km(anchor.lat, anchor.lng, float(lat), float(lng))

            pool.sort(key=distance)
    _apply_candidate(item, pool[0], day.date)
    return True


_CUISINE_HINTS = {
    "japanese": ("japanese", "sushi", "ramen", "izakaya", "yakitori", "tempura", "udon"),
    "sushi": ("sushi", "japanese"),
    "ramen": ("ramen", "japanese"),
    "chinese": ("chinese", "cantonese", "dim sum", "sichuan"),
    "korean": ("korean", "barbecue"),
    "thai": ("thai",),
    "italian": ("italian", "pizza", "pasta"),
    "indian": ("indian", "curry"),
}


def _cuisine(message: str) -> str | None:
    # The cuisine slot; a language phrase ("in Traditional Chinese") never fills it.
    return cuisine_slot(message)


def _search_extra(message: str) -> dict[str, str] | None:
    extra: dict[str, str] = {}
    cuisine = _cuisine(message)
    if cuisine:
        extra["meals_query"] = f"{cuisine} restaurant"
    places = _places_query(message)
    if places:
        extra["places_query"] = places
    return extra or None


def _places_query(message: str) -> str | None:
    wanted = [word for word in _ADD_KEYWORDS if re.search(rf"\b{word}s?\b", message.lower())]
    return " ".join(f"{word}s" for word in wanted) if wanted else None


_MEAL_WORDS = re.compile(
    r"\b(eat|eating|food|meal|dinner|lunch|breakfast|restaurant|cafe|café)\b",
    re.IGNORECASE,
)
_HOTEL_WORDS = re.compile(
    r"\b(hotel|hostel|stay|accommodation|room|ryokan)\b",
    re.IGNORECASE,
)
_CHANGE_WORDS = re.compile(
    r"\b(switch|swap|replace|change|another|different|instead)\b",
    re.IGNORECASE,
)


@dataclass
class PlanAdjust:
    """A pace change. Numbers outside 1–4 places a day are refused."""

    requested: bool = False
    places_per_day: int | None = None
    meals: str | None = None
    earliest: str | None = None
    min_hours: float | None = None
    reason: str = ""


_COUNT_WORDS = {
    "zero": 0, "none": 0, "no": 0,
    "one": 1, "single": 1, "a": 1, "an": 1,
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
}
_PLACE_WORD = r"(?:place|stop|sight|attraction|visit)"
_MEAL_WORD = r"(?:meal|lunch|dinner|breakfast|dining|restaurant|eat)"
_MAX_PLACES_A_DAY = 4


def plan_shape(message: str) -> bool:
    """True when the sentence asks to reshape the plan rather than one stop."""
    return interpret_plan(message).requested


def interpret_plan(message: str, hinted: dict | None = None) -> PlanAdjust:
    """Read a pace or meal rule from any wording, then refuse the extremes.

    The model may also pass places_per_day and meals in new_values. Those are
    clamped the same way. Hotel, flights and named stops are not touched here.
    """
    lower = message.lower()
    found = PlanAdjust()
    if re.search(r"\b(no places|no stops|no sights|empty itinerary|delete the trip|erase the trip)\b", lower):
        return PlanAdjust(
            requested=True,
            reason="That would remove the trip itself. I can thin a day, not erase it, so nothing was changed.",
        )
    count = re.search(
        rf"\b(\d+|zero|none|no|one|single|two|three|four|five|six)\b.{{0,30}}\b{_PLACE_WORD}s?\b",
        lower,
    )
    about_days = bool(re.search(r"\b(each day|every day|per day|a day)\b", lower))
    if count and (about_days or re.search(r"\b(only|just)\b", lower)):
        raw = count.group(1)
        number = int(raw) if raw.isdigit() else _COUNT_WORDS.get(raw)
        found.requested = True
        if number is None or number < 1 or number > _MAX_PLACES_A_DAY:
            shown = raw if number is None else str(number)
            found.reason = (
                f"I can keep 1 to {_MAX_PLACES_A_DAY} places a day. "
                f"{shown} is outside that, so the plan was not changed."
            )
        else:
            found.places_per_day = number
    if re.search(rf"\b{_MEAL_WORD}", lower):
        drop = bool(re.search(r"\b(no|without|skip|not required|don't need|do not need|drop|remove)\b", lower))
        if drop:
            found.requested = True
            found.meals = "drop"
    hinted = hinted or {}
    if isinstance(hinted.get("places_per_day"), int):
        number = hinted["places_per_day"]
        found.requested = True
        if number < 1 or number > _MAX_PLACES_A_DAY:
            found.places_per_day = None
            found.reason = (
                f"I can keep 1 to {_MAX_PLACES_A_DAY} places a day. "
                f"{number} is outside that, so the plan was not changed."
            )
        elif not found.reason:
            found.places_per_day = number
    if hinted.get("meals") in {"drop", "keep"} and not found.reason:
        found.requested = True
        found.meals = str(hinted["meals"])
    _read_schedule(lower, found)
    if isinstance(hinted.get("min_hours"), (int, float)) and not found.reason:
        hours = float(hinted["min_hours"])
        found.requested = True
        if hours < 1 or hours > 6:
            found.reason = "I can give a place 1 to 6 hours. Longer than that does not fit a day, so nothing was changed."
            found.min_hours = None
        else:
            found.min_hours = hours
    if isinstance(hinted.get("earliest"), str) and re.fullmatch(r"\d{2}:\d{2}", hinted["earliest"]) and not found.reason:
        found.requested = True
        found.earliest = hinted["earliest"]
    if found.earliest and found.min_hours and not found.reason:
        start = _clock_minutes(found.earliest)
        if start + int(found.min_hours * 60) > _DAY_END:
            found.reason = (
                f"A day ends at 21:30, so a {found.min_hours:g}-hour visit cannot start at {found.earliest}. "
                "Nothing was changed."
            )
            found.earliest = None
            found.min_hours = None
    return found


def apply_plan_shape(plan: TripPlan, shape: PlanAdjust) -> str:
    """Apply a pace rule that already passed the bounds check."""
    kept: list[str] = []
    dropped_places: list[str] = []
    dropped_meals: list[str] = []
    for index, day in enumerate(plan.days, start=1):
        if shape.meals == "drop":
            # A confirmed or locked row stays, whatever the pace (Proposal DB-09).
            meals = [item for item in day.items if item.type == ItemType.MEAL and not (item.locked or item.confirmed)]
            dropped_meals.extend(item.name for item in meals)
            meal_ids = {item.id for item in meals}
            day.items = [item for item in day.items if item.id not in meal_ids]
        if shape.places_per_day is not None:
            sights = [item for item in day.items if item.type == ItemType.ATTRACTION]
            if len(sights) > shape.places_per_day:
                protected = [item for item in sights if item.locked or item.confirmed]
                room = max(shape.places_per_day - len(protected), 0)
                free = [item for item in sights if not (item.locked or item.confirmed)]
                keep_ids = {item.id for item in protected} | {item.id for item in free[:room]}
                drop = [item for item in sights if item.id not in keep_ids]
                drop_ids = {item.id for item in drop}
                dropped_places.extend(item.name for item in drop)
                day.items = [item for item in day.items if item.id not in drop_ids]
                sights = [item for item in sights if item.id in keep_ids]
            if sights:
                kept.append(f"Day {index}: " + ", ".join(item.name for item in sights))
    parts: list[str] = []
    if shape.places_per_day is not None:
        parts.append(
            f"Each day now has at most {shape.places_per_day} "
            + ("place" if shape.places_per_day == 1 else "places")
            + (f" ({'; '.join(kept)})" if kept else "")
            + "."
        )
        if dropped_places:
            parts.append("Removed " + ", ".join(dropped_places) + ".")
    if shape.meals == "drop":
        parts.append(
            "Removed meals: " + ", ".join(dropped_meals) + "."
            if dropped_meals
            else "No meals were on the plan."
        )
    if shape.earliest or shape.min_hours:
        moved, dropped = _retime(plan, shape)
        if moved:
            parts.append("Visits now start at " + ", ".join(moved) + ".")
        if dropped:
            parts.append(
                "Could not fit "
                + ", ".join(dropped)
                + " after that start without running past 21:30, so those stops were removed."
            )
        if not moved and not dropped:
            parts.append("No visit needed a new time.")
    if not parts:
        parts.append("The plan already matched that pace.")
    parts.append("Hotel and flights are unchanged.")
    return " ".join(parts)


_DAY_END = 21 * 60 + 30
_MIN_START = 8 * 60
_MAX_START = 18 * 60


def _clock_minutes(value: str) -> int:
    hour, minute = value.split(":")
    return int(hour) * 60 + int(minute)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _read_schedule(lower: str, found: PlanAdjust) -> None:
    """A start time or a dwell, in whatever form the sentence uses."""
    if found.reason:
        return
    clock = re.search(
        r"\b(?:after|from|past|at)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b",
        lower,
    )
    compact = re.search(r"\b(?:after|from|past|at)\s+(\d{3,4})\b", lower)
    earliest: int | None = None
    if compact:
        raw = compact.group(1).zfill(4)
        earliest = int(raw[:2]) * 60 + int(raw[2:])
    elif clock:
        hour = int(clock.group(1))
        minute = int(clock.group(2) or 0)
        meridiem = clock.group(3)
        if meridiem == "pm" and hour < 12:
            hour += 12
        if meridiem == "am" and hour == 12:
            hour = 0
        earliest = hour * 60 + minute
    elif re.search(r"\b(can'?t|cannot|not)\b.{0,24}\b(morning|wake)\b", lower):
        earliest = 11 * 60
    hours = re.search(r"\b(\d+(?:\.\d+)?)\s+hours?\b", lower)
    if hours and re.search(r"\b(spend|least|minimum|stay|at a|single|spot|place)\b", lower):
        found.requested = True
        amount = float(hours.group(1))
        if amount < 1 or amount > 6:
            found.reason = (
                "I can give a place 1 to 6 hours. Longer than that does not fit a day, "
                "so nothing was changed."
            )
            return
        found.min_hours = amount
    if earliest is None:
        return
    found.requested = True
    if earliest < _MIN_START or earliest > _MAX_START:
        found.reason = (
            f"I can start the day between 08:00 and 18:00. {_hhmm(earliest)} is outside that, "
            "so nothing was changed."
        )
        return
    found.earliest = _hhmm(earliest)


def _retime(plan: TripPlan, shape: PlanAdjust) -> tuple[list[str], list[str]]:
    """Move sights to the requested start and length. Flights stay where they are."""
    earliest = _clock_minutes(shape.earliest) if shape.earliest else None
    moved: list[str] = []
    dropped: list[str] = []
    for index, day in enumerate(plan.days, start=1):
        cursor = earliest if earliest is not None else None
        remove: set[str] = set()
        for item in list(day.items):
            if item.type is not ItemType.ATTRACTION:
                continue
            try:
                current = _clock_minutes(item.start)
                end = _clock_minutes(item.end)
            except (ValueError, AttributeError):
                current, end = 9 * 60, 10 * 60 + 30
            duration = max(30, end - current)
            if shape.min_hours:
                duration = max(duration, int(shape.min_hours * 60))
            start = current if cursor is None else max(cursor, earliest or current)
            if start + duration > _DAY_END:
                remove.add(item.id)
                dropped.append(f"Day {index} {item.name}")
                continue
            item.start = _hhmm(start)
            item.end = _hhmm(start + duration)
            moved.append(f"Day {index} {item.name} {_hhmm(start)}–{_hhmm(start + duration)}")
            cursor = start + duration + 20
        if remove:
            day.items = [item for item in day.items if item.id not in remove]
    return moved, dropped


def resolve_referred(plan: TripPlan, message: str, scope_item_id: str | None) -> str | None:
    """The one item this message is about, or None when the edit is section-wide.

    A name in the sentence wins. Otherwise the selected chip ("this") is the item.
    """
    named = _name_mentioned(plan, message)
    if named:
        return named
    if scope_item_id and (plan.find_item(scope_item_id) or plan.find_hotel(scope_item_id)):
        return scope_item_id
    return None


def _name_mentioned(plan: TripPlan, message: str) -> str | None:
    lowered = message.lower()
    found: list[tuple[int, str]] = []
    for _, item in plan.iter_items():
        if len(item.name) >= 4 and item.name.lower() in lowered:
            found.append((len(item.name), item.id))
    for hotel in plan.hotels:
        if len(hotel.name) >= 4 and hotel.name.lower() in lowered:
            found.append((len(hotel.name), hotel.id))
    if not found:
        return None
    found.sort(reverse=True)
    return found[0][1]


def keep_only(original: TripPlan, updated: TripPlan, item_id: str) -> None:
    """Put back every row except the one the traveller referred to."""
    changed_item = None
    for day in updated.days:
        for item in day.items:
            if item.id == item_id:
                changed_item = item.model_copy(deep=True)
    changed_hotel = next((hotel.model_copy(deep=True) for hotel in updated.hotels if hotel.id == item_id), None)
    fresh_gaps = [
        gap for gap in updated.data_quality.gaps if gap not in original.data_quality.gaps
    ]

    updated.hotels = [hotel.model_copy(deep=True) for hotel in original.hotels]
    updated.tickets = [ticket.model_copy(deep=True) for ticket in original.tickets]
    updated.dates = original.dates.model_copy(deep=True)
    updated.days = [day.model_copy(deep=True) for day in original.days]
    updated.data_quality = original.data_quality.model_copy(deep=True)

    if changed_item is not None:
        for day in updated.days:
            for index, item in enumerate(day.items):
                if item.id != item_id:
                    continue
                same = changed_item.name == item.name
                day.items[index] = item.model_copy(
                    update={
                        "name": changed_item.name,
                        "geo": changed_item.geo,
                        "address": changed_item.address,
                        "notes": changed_item.notes,
                        "source_refs": changed_item.source_refs,
                        "tags": changed_item.tags,
                        "data_status": changed_item.data_status,
                        "fetched_at": changed_item.fetched_at,
                        "opening_hours": changed_item.opening_hours,
                        "hours_status": changed_item.hours_status,
                    }
                )
                if same and fresh_gaps:
                    updated.data_quality.gaps.append(fresh_gaps[-1])
                return
    if changed_hotel is not None:
        for index, hotel in enumerate(updated.hotels):
            if hotel.id == item_id:
                updated.hotels[index] = changed_hotel
                return
    if original.find_item(item_id) is not None:
        # The referred stop is gone from the edited plan: it was removed, so it
        # must stay out of the restored days (a replacement keeps its id).
        for day in updated.days:
            day.items = [item for item in day.items if item.id != item_id]
    if fresh_gaps:
        updated.data_quality.gaps.append(fresh_gaps[-1])


def _one_item_summary(original: TripPlan, updated: TripPlan, item_id: str) -> str:
    before = original.find_item(item_id)
    after = updated.find_item(item_id)
    if before and after:
        if before[1].name == after[1].name:
            return f"{before[1].name} stayed as it was. Nothing else was changed."
        return f"Replaced {before[1].name} with {after[1].name}. Everything else is unchanged."
    if before:
        return f"Removed {before[1].name}. Everything else is unchanged."
    old_hotel = original.find_hotel(item_id)
    new_hotel = updated.find_hotel(item_id)
    if old_hotel and new_hotel:
        if old_hotel.name == new_hotel.name:
            return f"{old_hotel.name} stayed as it was. Nothing else was changed."
        return f"Replaced {old_hotel.name} with {new_hotel.name}. Everything else is unchanged."
    return "Nothing else was changed."


def lock_scope(
    agent_names: set[AgentName],
    plan: TripPlan,
    message: str,
    scope_item_id: str | None,
) -> set[AgentName]:
    """A selected meal is a meal change. It must not rewrite the hotel.

    "switch this, I don't want to eat here" was replacing the hotel, including
    once with a stay 11,000 km away. Eating words, or a change aimed at the
    selected stop, stay on that stop unless the message names a hotel.
    """
    names = set(agent_names)
    if _HOTEL_WORDS.search(message):
        return names
    scoped = plan.find_item(scope_item_id) if scope_item_id else None
    about_meal = _MEAL_WORDS.search(message) is not None
    about_change = _CHANGE_WORDS.search(message) is not None
    if about_meal or (scoped is not None and about_change):
        names.discard(AgentName.HOTEL)
        names.discard(AgentName.TICKET)
        names.add(AgentName.ATTRACTION)
    return names


def _hotel_near(plan: TripPlan, row: dict, km: float = 40) -> bool:
    """A stay in another country is not a candidate for this city."""
    return _row_near(plan.destination.geo, row, km)


def _row_near(anchor, row: dict, km: float) -> bool:
    if anchor is None:
        return True
    geo = row.get("geo") or {}
    lat, lng = geo.get("lat"), geo.get("lng")
    if lat is None or lng is None:
        return True
    return haversine_km(anchor.lat, anchor.lng, float(lat), float(lng)) <= km


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
        hours, hours_status = _stated_hours(row, day.date)
        item = PlanItem(
            opening_hours=hours,
            hours_status=hours_status,
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
            names=trusted_names(row.get("names")),
        )
        day.items.append(item)
        day.items.sort(key=lambda entry: entry.start)
        added.append(item.name)
    return added


def _remove_attraction(plan: TripPlan, draft: ChangeDraft, message: str) -> str | None:
    resolved = _resolve_target(plan, draft, message)
    if resolved is not None:
        day, item = resolved
        if item.locked or item.confirmed:
            return None
        day.items.remove(item)
        return item.name
    # No specific stop was named: fall back to the keyword the traveller used.
    wanted = [word for word in _ADD_KEYWORDS if re.search(rf"\b{word}s?\b", message.lower())]
    for day in plan.days:
        for item in list(day.items):
            # A selected stop can be a meal as well as an attraction; the scope
            # chip is about the row the traveller tapped.
            if item.locked or item.confirmed:
                continue
            if not wanted or not any(word in item.name.lower() for word in wanted):
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
