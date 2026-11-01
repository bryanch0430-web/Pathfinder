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
            payload = attraction_result.payload if attraction_result else {}
            places = payload.get("places") or []
            meals = payload.get("meals") or []
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
                resolved = _resolve_target(updated, draft, message)
                if resolved is None:
                    updated.data_quality.gaps.append(
                        _gap(
                            "attractions",
                            "I could not tell which stop you meant. Name it or give its number in the day "
                            "(for example \"3. Ho Lee Fook\"), or select it first; the itinerary stayed as it was.",
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
                        replaced = _replace_target(
                            updated,
                            places,
                            meals,
                            (day, target_item),
                            message,
                            distance_aware=_FAR_MOTION.search(message) is not None,
                            prefer_name=wanted_text,
                        )
                        if replaced:
                            draft.description = f"Replaced {before} with {target_item.name}."
                        else:
                            detail = (
                                f"I could not find a verified {wanted_text!r} in the configured sources"
                                if wanted_text
                                else f"No verified alternative for {before!r} came back from the configured sources"
                            )
                            updated.data_quality.gaps.append(
                                _gap(
                                    "attractions",
                                    f"{detail}, so that stop stayed as it was.",
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
        updated.updated_at = utcnow()
        updated.change_summary = draft.description or "Applied a local change."
        updated.data_quality.notes = _merge_notes(updated.data_quality.notes, draft.description)

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


def _items_in_order(plan: TripPlan):
    for day in plan.days:
        for item in day.items:
            yield day, item


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


def _apply_candidate(item: PlanItem, row: dict) -> None:
    item.name = row["name"]
    item.geo = row.get("geo") or item.geo
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
    _apply_candidate(item, pool[0])
    return True


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
