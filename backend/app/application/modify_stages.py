"""The modify path's stages, as pure functions.

``modification.py`` is large because the edit has to read a sentence, resolve a stop, search
sources and phrase a reply. The part that can be described without any of that is here: which
agents an edit needs, how their evidence is merged into the plan, what a locked stop protects, and
what moving the trip's dates does to the plan. Each function takes data and returns data: no store,
no model, no toolbox. The big module calls them in order, so the order is visible:

    RouteDecision(affected_parts)
        -> select_agents_for_parts(...)        # parts -> agents
        -> agents_needed_for_edit(...)         # drop the agents this edit does not need
        -> merge_agent_results(...)            # evidence -> plan gaps
        -> apply_date_or_budget_change(...)    # dates move, or nothing
        -> preserve_locked_and_confirmed(...)  # rebuild -> locked/confirmed stops back
"""

from __future__ import annotations

from datetime import date
from typing import Any, Collection, Sequence

from app.domain.models import (
    AgentName,
    DataGap,
    DateRange,
    PlanItem,
    TripPlan,
    WeatherDay,
)

#: The plan section each agent owns.
AGENT_SECTION: dict[AgentName, str] = {
    AgentName.ATTRACTION: "attractions",
    AgentName.HOTEL: "hotels",
    AgentName.WEATHER: "weather",
    AgentName.TICKET: "tickets",
}

#: Which agents own one part of the trip. ``dates`` re-runs the three sections that depend on the calendar.
PART_AGENTS: dict[str, set[AgentName]] = {
    "hotel": {AgentName.HOTEL},
    "budget": {AgentName.HOTEL},
    "attraction": {AgentName.ATTRACTION},
    "ticket": {AgentName.TICKET},
    "weather": {AgentName.WEATHER},
    "dates": {AgentName.WEATHER, AgentName.HOTEL, AgentName.TICKET},
}

#: Parts that name a section of the plan rather than a day. A ``day_N`` part is a scope, not an agent.
PLAN_PARTS: frozenset[str] = frozenset(PART_AGENTS)


def clean_parts(parts: Sequence[str]) -> list[str]:
    """The parts that name a section or a day. Anything else is a model's word we do not act on."""
    return [part for part in parts if part in PLAN_PARTS or str(part).startswith("day_")]


def select_agents_for_parts(
    affected_parts: Sequence[str],
    *,
    dates_move: bool,
    form_changes: Sequence[str] = (),
    upscale: bool = False,
    nothing_stated: bool = False,
) -> set[AgentName]:
    """Which agents own the parts an edit named (Proposal DB-09).

    ``dates_move`` adds the calendar agents and drops the attraction agent when the edit named no
    attraction: moving the dates is not a reason to look for new stops. ``form_changes`` are the trip
    details the page's own "generate again" changed; a party, origin or budget change prices the stay
    and the flights again, and no form change looks for a new attraction. An empty answer falls back to
    the attraction agent, as the edit always did.
    """
    parts = clean_parts(affected_parts)
    names: set[AgentName] = set()
    for part in parts:
        if part == "dates" and not dates_move:
            continue
        names |= PART_AGENTS.get(part, {AgentName.ATTRACTION})
    if form_changes:
        names.discard(AgentName.ATTRACTION)
        if {"party", "origin", "budget"} & set(form_changes):
            names |= {AgentName.HOTEL, AgentName.TICKET}
    if upscale and not form_changes:
        names.add(AgentName.HOTEL)
    if dates_move:
        names |= PART_AGENTS["dates"]
        if "attraction" not in parts:
            names.discard(AgentName.ATTRACTION)
    if not names and not form_changes and not nothing_stated:
        names = {AgentName.ATTRACTION}
    return names


def agents_needed_for_edit(
    names: Collection[AgentName],
    *,
    shape_requested: bool = False,
    referred: str | None = None,
    remove_requested: bool = False,
    add_requested: bool = False,
    move_requested: bool = False,
) -> set[AgentName]:
    """The agents whose data this edit actually needs, given what the sentence turned out to ask.

    A pace or meal rule works from the plan alone. Taking a stop out needs no new places. A "move"
    is about a stop already on the plan. ``referred`` is only read to tell whether a shape request is
    about the whole plan or about one pointed-at row (whose agent is already in ``names``).
    """
    needed = set(names)
    if shape_requested and referred is None:
        needed = set()
    elif remove_requested and not add_requested:
        needed.discard(AgentName.ATTRACTION)
    if move_requested:
        needed = set()
    return needed


def merge_agent_results(
    plan: TripPlan,
    results: dict[str, Any],
    *,
    refreshed_sections: Collection[str] = (),
    apply_weather: bool = False,
) -> TripPlan:
    """Merge freshly fetched agent evidence into a copy of the plan.

    The deterministic half of the merge, and the only half a unit test needs: a section that was
    fetched again gets a fresh gap verdict (an old Wikimedia/weather/ticket gap does not survive a
    successful retry), what an agent could not fetch is added once, and - when ``apply_weather`` is
    set - the forecast rows for the plan's own dates replace those days' weather. The date move is
    applied by :func:`apply_date_or_budget_change` *before* the weather is merged, so a forecast for
    the new dates is not cleared as a forecast for the old ones.
    """
    updated = plan.model_copy(deep=True)
    refreshed = set(refreshed_sections)
    if refreshed:
        updated.data_quality.gaps = [gap for gap in updated.data_quality.gaps if gap.section not in refreshed]
    for result in (results or {}).values():
        failures = getattr(result, "failures", None) or []
        for gap in failures:
            if not any(
                known.section == gap.section and known.message == gap.message
                for known in updated.data_quality.gaps
            ):
                updated.data_quality.gaps.append(gap)
    if apply_weather:
        updated = apply_weather_results(updated, results)
    return updated


def apply_weather_results(plan: TripPlan, results: dict[str, Any]) -> TripPlan:
    """Put the weather rows the weather agent returned on the days they belong to, leaving other days alone."""
    updated = plan.model_copy(deep=True)
    weather_result = (results or {}).get("weather") or (results or {}).get(AgentName.WEATHER)
    rows = (getattr(weather_result, "payload", None) or {}).get("days") or []
    if not rows:
        return updated
    by_date = {row.get("date"): row for row in rows if isinstance(row, dict)}
    for day in updated.days:
        row = by_date.get(day.date.isoformat())
        if row:
            try:
                day.weather = WeatherDay.model_validate(row)
            except Exception:
                # A malformed row is not applied: the day keeps what it had rather than an invented one.
                continue
    return updated


def apply_date_or_budget_change(
    plan: TripPlan,
    *,
    new_range: DateRange | None = None,
    today: date | None = None,
) -> TripPlan:
    """Move a plan to ``new_range``; a budget change passes no range and leaves the plan alone.

    Moving the dates is deterministic and happens before any candidate is merged: every day gets its
    new date, a forecast for the old date is cleared (the day shows none until a new one arrives), the
    stay takes the new check-in/out, and a flight that no longer fits the range is dropped. What the
    earlier dates said about being past is removed so the new dates can say their own thing. Opening
    hours that the move invalidated are added as a gap.
    """
    if new_range is None:
        return plan.model_copy(deep=True)
    from app.application.modification import PAST_DATES, _gap, _hours_after_a_move, _ticket_within

    updated = plan.model_copy(deep=True)
    updated.data_quality.gaps = [gap for gap in updated.data_quality.gaps if gap.section != "dates"]
    if new_range.start < (today or date.today()):
        updated.data_quality.gaps.append(_gap("dates", PAST_DATES))
    updated.dates = new_range
    for index, day in enumerate(updated.days):
        day.date = new_range.dates[index]
        if day.weather is not None and day.weather.date != day.date:
            # That forecast is for the old dates. Without a new one the day shows none.
            day.weather = None
    if updated.hotels:
        updated.first_stay.check_in = new_range.start
        updated.first_stay.check_out = new_range.end
    updated.tickets = [ticket for ticket in updated.tickets if _ticket_within(ticket, new_range)]
    updated.data_quality.gaps += [_gap("attractions", text) for text in _hours_after_a_move(updated)]
    return updated


def preserve_locked_and_confirmed(
    old: TripPlan,
    new: TripPlan,
    blocked: Collection[date] = (),
) -> list[str]:
    """Put the traveller's locked and confirmed stops back after a rebuild; return what the plan should say.

    A pure read of both plans and a write on ``new`` (the rebuild's own row for the same place is
    replaced, not duplicated). The placement rules - one copy of each, the day it was on or the nearest
    day with room, no overlapping clocks - live in ``modification._carry_protected``; this wrapper is the
    stage the modify entry point calls, and it exists so the stage order is readable in one place.
    """
    from app.application.modification import _carry_protected

    return _carry_protected(old, new, blocked)


def protected_items(plan: TripPlan) -> list[PlanItem]:
    """The stops an edit may not move: locked or confirmed, in the order the plan holds them."""
    return [
        item
        for day in plan.days
        for item in sorted(day.items, key=lambda entry: entry.start)
        if item.locked or item.confirmed
    ]


def gaps_for(results: dict[str, Any]) -> list[DataGap]:
    """Every failure of every agent result, once, in the order the agents reported them."""
    found: list[DataGap] = []
    for result in (results or {}).values():
        for gap in getattr(result, "failures", None) or []:
            if not any(known.section == gap.section and known.message == gap.message for known in found):
                found.append(gap)
    return found
