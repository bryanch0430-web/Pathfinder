"""The refine loop: check a composed plan, fix what can be fixed deterministically, note the rest.

``evaluate_plan`` reads the plan against the facts it already carries (each stop's source hours,
its transfer or the app's transport model, the cost and budget, the traveller's stated diet and
avoid wishes) and returns plain-English issues. ``refine_plan`` runs evaluate -> fix -> evaluate
for at most ``max_rounds`` rounds. The fixes only rearrange what the plan already holds: swap a
stop for one of its own source-backed alternatives (``choices.swap_option``), reorder a day's
stops, move a meal inside its slot, move the weakest stop of an overloaded day into a nearby
stop's alternatives, push stops later when a transfer does not fit. A budget overrun never
switches the stay or flight; it names a cheaper option already in the plan. Whatever is left
becomes a plan note. No model call, no network call, no invented fact.

A day whose stops overlap is a structural error for ``validate_plan``/``repair_plan``; the loop
leaves it as it is.
"""

from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from datetime import date
from itertools import permutations
from typing import Any, Literal

from app.application.choices import _as_alternative, stay_dates_match, swap_option
from app.application.opening_hours import windows_on
from app.application.planner import (
    DAY_LAST_END,
    PACE_BUFFER_MINUTES,
    SNACK_EARLIEST,
    SNACK_LATEST,
    _diet,
    _is_must_see,
    _must_see,
    _pace,
    _required_names,
    _serves_diet,
)
from app.application.transfers import refresh_transfers, with_transfer_note
from app.application.trip_rules import flight_windows
from app.domain.models import DayPlan, ItemType, PlanAlternative, PlanItem, TripContext, TripPlan, selected_ticket
from app.infrastructure.http import haversine_km
from app.infrastructure.providers.routes import model_travel

IssueSeverity = Literal["error", "warn"]

CLOSED = "closed"
OUTSIDE_HOURS = "outside_hours"
TRAVEL_GAP = "travel_gap"
INEFFICIENT_ORDER = "inefficient_order"
MEAL_WINDOW = "meal_window"
DAY_HOURS = "day_hours"
DAY_WALKING = "day_walking"
BUDGET_OVER = "budget_over"
PREFERENCE = "preference"

#: Issue codes the loop has a deterministic fix for; the others only become notes.
FIXABLE = {CLOSED, OUTSIDE_HOURS, TRAVEL_GAP, INEFFICIENT_ORDER, MEAL_WINDOW, DAY_HOURS, DAY_WALKING, PREFERENCE}

#: When each meal may start, minutes after midnight. The planner's own times (breakfast 08:00,
#: lunch 12:30 to 14:00, dinner 19:00 to 21:00, the cafe stop SNACK_EARLIEST to SNACK_LATEST) sit inside.
MEAL_WINDOWS = {
    "breakfast": (7 * 60, 10 * 60),
    "lunch": (11 * 60 + 30, 14 * 60 + 30),
    "dinner": (17 * 60 + 30, 21 * 60 + 30),
    "snack": (SNACK_EARLIEST, SNACK_LATEST),
}
#: A reorder is worth it only when it saves more than this share of the day's distance ...
REORDER_MIN_SAVING = 0.20
#: ... and at least this many kilometres.
REORDER_MIN_KM = 1.0
#: Runs of stops up to this long are reordered by trying every order; longer ones by 2-opt.
REORDER_BRUTE_FORCE_RUN = 5
#: Opens a note that states an issue the loop could not fix; replaced on every run.
CHECK_PREFIX = "Plan check: "
#: Opens a note that states a change the loop made; kept.
CHANGE_PREFIX = "Plan change: "
#: Most issue notes written on the plan.
MAX_CHECK_NOTES = 6
#: Most change notes kept on the plan (the latest ones).
MAX_CHANGE_NOTES = 6

_WEEKDAY = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_AVOID = re.compile(r"\b(?:avoid|avoiding|no|skip|without|not)\s+(?:any\s+|the\s+)?([a-z][a-z\-]{2,})", re.IGNORECASE)
#: Words after "no"/"not" that are not a kind of place ("no more than", "not too far").
_AVOID_NOISE = {
    "more", "less", "than", "too", "much", "many", "later", "earlier", "early", "late", "longer", "rush",
    "need", "needed", "problem", "preference", "budget", "limit", "flights", "flight", "hotel", "hotels",
    "car", "driving", "stairs", "walking", "walks", "hurry", "pressure", "plan", "include", "visit", "only",
    "one", "two", "three", "day", "days", "night", "nights", "before", "after", "pm", "am",
}


@dataclass(frozen=True)
class PlanIssue:
    code: str
    day: int | None
    item_ids: tuple[str, ...]
    severity: IssueSeverity
    message: str


@dataclass(frozen=True)
class RefineLimits:
    max_activity_hours: float = 9.0
    max_walking_km: float = 12.0

    @classmethod
    def from_settings(cls, settings: Any | None) -> "RefineLimits":
        if settings is None:
            return cls()
        return cls(
            max_activity_hours=float(getattr(settings, "day_max_activity_hours", cls.max_activity_hours)),
            max_walking_km=float(getattr(settings, "day_max_walking_km", cls.max_walking_km)),
        )


@dataclass
class _Context:
    """What the checks read from the trip form, worked out once."""

    buffer: int
    protected: list[str]
    keep_ids: set[str]
    diet: str | None
    avoid: list[str]
    limits: RefineLimits
    day_ends: dict[date, int]


# --- evaluate ----------------------------------------------------------------


def evaluate_plan(
    plan: TripPlan,
    context: TripContext | None,
    *,
    limits: RefineLimits | None = None,
    keep_ids: set[str] | frozenset[str] | tuple[str, ...] = (),
) -> list[PlanIssue]:
    """The plan's issues, in day order. Pure: ``plan`` is not changed."""
    ctx = _context(plan, context, limits, keep_ids)
    issues: list[PlanIssue] = []
    for index, day in enumerate(plan.days):
        ordered = _ordered(day)
        issues.extend(_hours_issues(index, day, ordered))
        issues.extend(_gap_issues(index, ordered))
        issues.extend(_meal_issues(index, day, ordered))
        issues.extend(_load_issues(index, day, ordered, ctx.limits))
        issues.extend(_preference_issues(index, ordered, ctx))
        better = _better_order(day, ordered, ctx)
        if better is not None:
            _, _, old_km, new_km = better
            issues.append(
                PlanIssue(
                    INEFFICIENT_ORDER, index, tuple(item.id for item in ordered if item.type is ItemType.ATTRACTION),
                    "warn",
                    f"On {_day_name(day.date)} another order of the stops covers {new_km:.1f} km instead of "
                    f"{old_km:.1f} km and still fits their opening hours.",
                )
            )
    budget = _budget_issue(plan)
    if budget is not None:
        issues.append(budget)
    return issues


def _hours_issues(index: int, day: DayPlan, ordered: list[PlanItem]) -> list[PlanIssue]:
    issues: list[PlanIssue] = []
    for item in ordered:
        if item.type not in {ItemType.ATTRACTION, ItemType.MEAL} or not item.opening_hours:
            continue
        windows = windows_on(item.opening_hours, day.date)
        if windows == []:
            issues.append(
                PlanIssue(
                    CLOSED, index, (item.id,), "error",
                    f"{item.name} is closed on {_day_name(day.date)}, according to its source's hours.",
                )
            )
        elif windows and not _fits(windows, _minutes(item.start), _minutes(item.end)):
            issues.append(
                PlanIssue(
                    OUTSIDE_HOURS, index, (item.id,), "error",
                    f"{item.name} is planned {item.start}-{item.end} on {_day_name(day.date)}, outside its stated hours "
                    f"({'; '.join(item.opening_hours)}).",
                )
            )
    return issues


def _gap_issues(index: int, ordered: list[PlanItem]) -> list[PlanIssue]:
    issues: list[PlanIssue] = []
    for previous, item in zip(ordered, ordered[1:]):
        gap = _minutes(item.start) - _minutes(previous.end)
        if gap < 0:
            continue  # an overlap is a structural error for validate_plan
        leg = _leg(previous, item)
        if leg is not None and leg[0] > gap:
            issues.append(
                PlanIssue(
                    TRAVEL_GAP, index, (previous.id, item.id), "error",
                    f"Getting from {previous.name} to {item.name} takes about {leg[0]} min, but only {gap} min "
                    "are left between them.",
                )
            )
    return issues


def _meal_issues(index: int, day: DayPlan, ordered: list[PlanItem]) -> list[PlanIssue]:
    issues: list[PlanIssue] = []
    for item in ordered:
        window = MEAL_WINDOWS.get(item.meal_slot or "") if item.type is ItemType.MEAL else None
        if window is None:
            continue
        start = _minutes(item.start)
        if not window[0] <= start <= window[1]:
            label = "cafe stop" if item.meal_slot == "snack" else item.meal_slot
            issues.append(
                PlanIssue(
                    MEAL_WINDOW, index, (item.id,), "warn",
                    f"The {label} at {item.name} on {_day_name(day.date)} starts at {item.start}, outside the usual "
                    f"{_hhmm(window[0])}-{_hhmm(window[1])}.",
                )
            )
    return issues


def _load_issues(index: int, day: DayPlan, ordered: list[PlanItem], limits: RefineLimits) -> list[PlanIssue]:
    issues: list[PlanIssue] = []
    stops = [item for item in ordered if item.type is ItemType.ATTRACTION]
    minutes = sum(max(0, _minutes(item.end) - _minutes(item.start)) for item in stops)
    if minutes > limits.max_activity_hours * 60:
        issues.append(
            PlanIssue(
                DAY_HOURS, index, tuple(item.id for item in stops), "warn",
                f"{_day_name(day.date)} has {minutes / 60:.1f} h of visits, more than {limits.max_activity_hours:g} h.",
            )
        )
    walking = _walking_km(ordered)
    if walking > limits.max_walking_km:
        issues.append(
            PlanIssue(
                DAY_WALKING, index, tuple(item.id for item in stops), "warn",
                f"{_day_name(day.date)} has about {walking:.1f} km on foot between stops, more than "
                f"{limits.max_walking_km:g} km.",
            )
        )
    return issues


def _preference_issues(index: int, ordered: list[PlanItem], ctx: _Context) -> list[PlanIssue]:
    issues: list[PlanIssue] = []
    for item in ordered:
        if item.type not in {ItemType.ATTRACTION, ItemType.MEAL}:
            continue
        reason = _conflict(item, item.type is ItemType.MEAL, ctx)
        if reason:
            issues.append(PlanIssue(PREFERENCE, index, (item.id,), "warn", f"{item.name} {reason}."))
    return issues


def _budget_issue(plan: TripPlan) -> PlanIssue | None:
    cost = plan.cost
    if cost.status != "over" or cost.budget is None:
        return None
    currency = cost.currency
    parts = [
        f"The verified cost is {currency} {cost.total:,.0f}, {cost.total - cost.budget:,.0f} over the "
        f"{currency} {cost.budget:,.0f} budget."
    ]
    stay = plan.hotels[0] if plan.hotels else None
    if stay is not None and stay.price_per_night is not None:
        cheaper = [
            option
            for option in plan.hotel_options
            if option.id != stay.id
            and stay_dates_match(option, stay)  # a rate for other dates is not a cheaper stay
            and option.price_per_night is not None
            and option.price_per_night.currency == stay.price_per_night.currency
            and option.price_per_night.amount < stay.price_per_night.amount
        ]
        if cheaper:
            best = min(cheaper, key=lambda option: option.price_per_night.amount)  # type: ignore[union-attr]
            rate = stay.price_per_night
            parts.append(
                f"{best.name} is a cheaper stay in the options ({rate.currency} "
                f"{best.price_per_night.amount:,.0f} a night against {rate.amount:,.0f} for {stay.name})."  # type: ignore[union-attr]
            )
    ticket = selected_ticket(plan.tickets)
    if ticket is not None and ticket.price is not None:
        cheaper_tickets = [
            option
            for option in plan.tickets
            if option.id != ticket.id
            and option.price is not None
            and option.price.currency == ticket.price.currency
            and option.price.amount < ticket.price.amount
        ]
        if cheaper_tickets:
            best_ticket = min(cheaper_tickets, key=lambda option: option.price.amount)  # type: ignore[union-attr]
            parts.append(
                f"{best_ticket.name} is a cheaper {best_ticket.mode} in the options ({ticket.price.currency} "
                f"{best_ticket.price.amount:,.0f} against {ticket.price.amount:,.0f} for {ticket.name})."  # type: ignore[union-attr]
            )
    return PlanIssue(BUDGET_OVER, None, (), "warn", " ".join(parts))


# --- refine ------------------------------------------------------------------


def refine_plan(
    plan: TripPlan,
    context: TripContext | None,
    max_rounds: int = 3,
    *,
    limits: RefineLimits | None = None,
    keep_ids: set[str] | frozenset[str] | tuple[str, ...] = (),
    only_days: set[int] | frozenset[int] | None = None,
    changes: list[str] | None = None,
) -> tuple[TripPlan, list[PlanIssue], int]:
    """A refined copy of ``plan``, the issues still on it, and the rounds of fixes run.

    ``keep_ids`` are stops the traveller just chose: they are never swapped or moved out. With
    ``only_days`` (day indexes) only those days are changed; the whole plan is still checked.
    Issues left at the end are written as plan notes (``CHECK_PREFIX``); every change the loop
    made is noted (``CHANGE_PREFIX``) and appended to ``changes`` when given. Transfers the loop
    made stale are cleared; ``refine_and_refresh`` fetches them again.
    """
    refined = plan.model_copy(deep=True)
    rounds = 0
    made: list[str] = []
    for _ in range(max(0, max_rounds)):
        issues = evaluate_plan(refined, context, limits=limits, keep_ids=keep_ids)
        if only_days is not None:
            issues = [issue for issue in issues if issue.day in only_days]
        if not any(issue.code in FIXABLE for issue in issues):
            break
        rounds += 1
        if not _apply_fixes(refined, context, issues, limits=limits, keep_ids=keep_ids, changes=made):
            break
    if changes is not None:
        changes.extend(made)
    changes = made
    remaining = evaluate_plan(refined, context, limits=limits, keep_ids=keep_ids)
    _write_notes(refined, remaining, changes)
    return refined, remaining, rounds


async def refine_and_refresh(
    plan: TripPlan,
    context: TripContext | None,
    *,
    settings: Any | None = None,
    max_rounds: int | None = None,
    toolbox: Any | None = None,
    transfers: Any | None = None,
    keep_ids: set[str] | frozenset[str] | tuple[str, ...] = (),
    only_days: set[int] | frozenset[int] | None = None,
    changes: list[str] | None = None,
) -> tuple[TripPlan, list[PlanIssue], int]:
    """``refine_plan`` (in a worker thread: it is CPU work), then the transfers of every day it changed.

    The transfers come through the toolbox's logged ``routes.segments`` call like the planner's own
    (no network with the transport model), and only for days whose stops or times changed.
    """
    rounds_cap = max_rounds if max_rounds is not None else int(getattr(settings, "refine_max_rounds", 3))
    refined, issues, rounds = await asyncio.to_thread(
        refine_plan,
        plan, context, max_rounds=rounds_cap, limits=RefineLimits.from_settings(settings), keep_ids=keep_ids,
        only_days=only_days, changes=changes,
    )
    if rounds:
        moved = await refresh_transfers(
            plan,
            refined,
            toolbox=toolbox,
            source=transfers,
            buffer=PACE_BUFFER_MINUTES.get(_pace(context) if context is not None else "balanced", 5),
        )
        if moved:
            # The fresh transfers pushed stops later: a change like any other, told the same way.
            names = list(dict.fromkeys(moved))
            told = [f"{', '.join(names)} start later to leave time for the fetched transfers."]
            if changes is not None:
                changes.extend(told)
            _write_notes(refined, issues, told)
    return refined, issues, rounds


def _apply_fixes(
    plan: TripPlan,
    context: TripContext | None,
    issues: list[PlanIssue],
    *,
    limits: RefineLimits | None,
    keep_ids: Any,
    changes: list[str],
) -> bool:
    ctx = _context(plan, context, limits, keep_ids)
    changed = False
    for index, day in enumerate(plan.days):
        day_issues = [issue for issue in issues if issue.day == index and issue.code in FIXABLE]
        if day_issues and _fix_day(plan, day, day_issues, ctx, changes):
            changed = True
    return changed


def _fix_day(plan: TripPlan, day: DayPlan, issues: list[PlanIssue], ctx: _Context, changes: list[str]) -> bool:
    """One kind of fix on one day per round; the next round sees its effect."""
    ordered = _ordered(day)
    if _overlaps(ordered):
        return False
    codes = {issue.code for issue in issues}
    by_id = {item.id: item for item in day.items}

    swapped: set[str] = set()
    for issue in issues:
        if issue.code not in {CLOSED, OUTSIDE_HOURS, PREFERENCE}:
            continue
        item = by_id.get(issue.item_ids[0]) if issue.item_ids else None
        if item is None or item.id in swapped or _protected(item, ctx):
            continue
        option = _open_alternative(item, day.date, ctx)
        if option is None:
            continue
        position = next(pos for pos, entry in enumerate(day.items) if entry.id == item.id)
        old = swap_option(plan, day, position, option)
        new = day.items[position]
        reason = "does not suit the stated preferences" if issue.code == PREFERENCE else "is closed at that time"
        sources = ", ".join(dict.fromkeys(ref.source for ref in new.source_refs if ref.source)) or "a travel source"
        new.notes = f"In place of {old.name}, which {reason}; from {sources}."
        changes.append(f"{new.name} replaces {old.name} on {_day_name(day.date)}: {old.name} {reason}.")
        swapped.add(item.id)
    if swapped:
        _settle_transfers(day, swapped)
        return True

    for issue in issues:
        if issue.code == MEAL_WINDOW and issue.item_ids:
            item = by_id.get(issue.item_ids[0])
            before = item.start if item is not None else ""
            if item is not None and not _protected(item, ctx) and _move_meal(day, item, ctx):
                label = "cafe stop" if item.meal_slot == "snack" else item.meal_slot
                changes.append(
                    f"The {label} at {item.name} on {_day_name(day.date)} moved from {before} to {item.start}, "
                    "inside its usual time."
                )
                return True

    if codes & {DAY_HOURS, DAY_WALKING} and _trim(plan, day, ctx, changes):
        return True

    if INEFFICIENT_ORDER in codes:
        better = _better_order(day, ordered, ctx)
        if better is not None:
            order, times, old_km, new_km = better
            _set_times(order, times)
            day.items = order
            _settle_transfers(day, set())
            changes.append(
                f"The stops on {_day_name(day.date)} are in a shorter order ({new_km:.1f} km instead of {old_km:.1f} km): "
                f"{', '.join(item.name for item in order if item.type is ItemType.ATTRACTION)}."
            )
            return True

    if TRAVEL_GAP in codes:
        times = _retime(ordered, day.date, ctx, compress=False)
        if times is not None and times != _times(ordered):
            moved = [
                f"{item.name} {item.start} to {_hhmm(start)}"
                for item, (start, _end) in zip(ordered, times)
                if _minutes(item.start) != start
            ]
            _set_times(ordered, times)
            day.items = ordered
            changes.append(
                f"Stops on {_day_name(day.date)} start later to leave time for the transfers: {', '.join(moved)}."
            )
            return True
    return False


def _open_alternative(item: PlanItem, day_date: date, ctx: _Context):
    """The item's best alternative that is open for its time and suits the stated wishes."""
    start, end = _minutes(item.start), _minutes(item.end)
    ranked = []
    for alt in item.alternatives:
        if item.type is ItemType.ATTRACTION and (not alt.source_refs or alt.geo is None):
            continue
        if _conflict(alt, item.type is ItemType.MEAL, ctx):
            continue
        if item.type is ItemType.MEAL and ctx.diet and not _labelled_for_diet(alt, ctx.diet):
            continue  # a meal swapped in under a diet must be one its source labels for it
        windows = windows_on(alt.opening_hours, day_date) if alt.opening_hours else None
        if windows == [] or (windows and not _fits(windows, start, end)):
            continue
        ranked.append((0 if windows else 1, alt.distance_m if alt.distance_m is not None else 10**9, alt))
    if not ranked:
        return None
    return min(ranked, key=lambda row: (row[0], row[1]))[2]


def _move_meal(day: DayPlan, item: PlanItem, ctx: _Context) -> bool:
    """Start the meal inside its slot without leaving the gap between its neighbours."""
    window = MEAL_WINDOWS.get(item.meal_slot or "")
    if window is None:
        return False
    ordered = _ordered(day)
    position = next(pos for pos, entry in enumerate(ordered) if entry.id == item.id)
    previous = ordered[position - 1] if position else None
    following = ordered[position + 1] if position + 1 < len(ordered) else None
    length = _minutes(item.end) - _minutes(item.start)
    earliest = window[0]
    if previous is not None:
        leg = _leg(previous, item)
        earliest = max(earliest, _minutes(previous.end) + (leg[0] + ctx.buffer if leg else 0))
    latest = window[1]
    if following is not None:
        leg = _leg(item, following)
        latest = min(latest, _minutes(following.start) - (leg[0] + ctx.buffer if leg else 0) - length)
    else:
        latest = min(latest, _day_end(day.date, ctx) - length)
    windows = windows_on(item.opening_hours, day.date) if item.opening_hours else None
    start = _round5(earliest)
    while start <= latest:
        if not windows or _fits(windows, start, start + length):
            item.start, item.end = _hhmm(start), _hhmm(start + length)
            return True
        start += 5
    return False


def _trim(plan: TripPlan, day: DayPlan, ctx: _Context, changes: list[str]) -> bool:
    """Move the day's weakest movable sight into the nearest kept sight's alternatives."""
    stops = [item for item in _ordered(day) if item.type is ItemType.ATTRACTION]
    movable = [item for item in stops if not _protected(item, ctx)]
    if not movable or len(stops) < 2:
        return False
    victim = min(reversed(movable), key=_value)
    kept = [item for item in stops if item.id != victim.id]
    host = min(
        kept,
        key=lambda item: _km(item, victim) if item.geo is not None and victim.geo is not None else float("inf"),
    )
    alternative = _as_alternative(victim)
    taken = {alt.id for alt in host.alternatives}
    if alternative.id in taken:
        alternative.id = f"{alternative.id}_{len(taken) + 1}"
    if host.geo is not None and victim.geo is not None:
        alternative.distance_m = int(round(_km(host, victim) * 1000))
    host.alternatives = [*host.alternatives, alternative]
    day.items = [item for item in day.items if item.id != victim.id]
    plan.reservations = [row for row in plan.reservations if row.name != victim.name]
    ordered = _ordered(day)
    times = _retime(ordered, day.date, ctx, compress=True)
    if times is not None:
        _set_times(ordered, times)
        day.items = ordered
    _settle_transfers(day, set())
    changes.append(
        f"{victim.name} moved to the options near {host.name} on {_day_name(day.date)}, to keep the day lighter."
    )
    return True


def _better_order(day: DayPlan, ordered: list[PlanItem], ctx: _Context):
    """A shorter order of the day's movable sights that still fits every stop's hours, or None.

    Meals, locked, confirmed and requested stops stay where they are; the sights between them
    are reordered (the day's first stop never moves, as in the planner's 2-opt).
    """
    if _overlaps(ordered) or len(ordered) < 3:
        return None

    def anchor(item: PlanItem) -> bool:
        return item.type is not ItemType.ATTRACTION or item.geo is None or _protected(item, ctx)

    order = list(ordered)
    position = 0
    while position < len(order):
        if anchor(order[position]):
            position += 1
            continue
        end = position
        while end < len(order) and not anchor(order[end]):
            end += 1
        run = order[position:end]
        before = order[position - 1] if position else None
        after = order[end] if end < len(order) else None
        head = [] if before is not None and before.geo is not None else run[:1]
        body = run[len(head):]
        if len(body) >= 2:
            lead = [before] if before is not None and before.geo is not None else head
            tail = [after] if after is not None and after.geo is not None else []
            if len(body) <= REORDER_BRUTE_FORCE_RUN:
                best = list(min(permutations(body), key=lambda perm: _path_km([*lead, *perm, *tail])))
            else:
                best = _two_opt(lead, body, tail)
            order[position:end] = [*head, *best]
        position = end
    if [item.id for item in order] == [item.id for item in ordered]:
        return None
    old_km, new_km = _path_km(ordered), _path_km(order)
    if old_km - new_km < REORDER_MIN_KM or new_km > old_km * (1 - REORDER_MIN_SAVING):
        return None
    times = _retime(order, day.date, ctx, compress=True)
    if times is None:
        return None
    return order, times, old_km, new_km


def _two_opt(lead: list[PlanItem], body: list[PlanItem], tail: list[PlanItem]) -> list[PlanItem]:
    """The planner's open-path 2-opt (``planner._two_opt``) on ``body``, between its fixed ends."""
    best = list(body)
    best_km = _path_km([*lead, *best, *tail])
    improved, passes = True, 0
    while improved and passes < 30:
        improved, passes = False, passes + 1
        for i in range(len(best) - 1):
            for j in range(i + 1, len(best)):
                candidate = best[:i] + list(reversed(best[i : j + 1])) + best[j + 1 :]
                km = _path_km([*lead, *candidate, *tail])
                if km + 1e-9 < best_km:
                    best, best_km, improved = candidate, km, True
    return best


def _retime(items: list[PlanItem], day_date: date, ctx: _Context, *, compress: bool) -> list[tuple[int, int]] | None:
    """Times for ``items`` in this order, each after the previous end plus its transfer and buffer.

    Lengths are kept. With ``compress`` a sight may start earlier than before; a meal never starts
    earlier than it was. None when a locked or confirmed stop would move, a stop would leave its
    stated hours or a meal its slot, or the day would run later than it may.
    """
    times: list[tuple[int, int]] = []
    previous: PlanItem | None = None
    previous_end = 0
    for item in items:
        start, end = _minutes(item.start), _minutes(item.end)
        length = end - start
        if previous is not None:
            leg = _leg(previous, item)
            earliest = previous_end + (leg[0] + ctx.buffer if leg else 0)
            new_start = earliest if compress and item.type is ItemType.ATTRACTION else max(earliest, start)
            new_start = _round5(new_start)
            windows = windows_on(item.opening_hours, day_date) if item.opening_hours else None
            if windows:
                placed = next(
                    (max(new_start, opens) for opens, closes in windows if _round5(max(new_start, opens)) + length <= closes),
                    None,
                )
                if placed is None:
                    return None
                new_start = _round5(placed)
            if (item.locked or item.confirmed) and new_start != start:
                return None
            window = MEAL_WINDOWS.get(item.meal_slot or "") if item.type is ItemType.MEAL else None
            if window is not None and window[0] <= start <= window[1] and not window[0] <= new_start <= window[1]:
                return None
            start, end = new_start, new_start + length
        times.append((start, end))
        previous, previous_end = item, end
    if times and times[-1][1] > max(_day_end(day_date, ctx), _minutes(items[-1].end)):
        return None
    return times


# --- notes -------------------------------------------------------------------


def _write_notes(plan: TripPlan, issues: list[PlanIssue], changes: list[str]) -> None:
    """Check notes are replaced; change notes are kept once each, the latest ``MAX_CHANGE_NOTES`` of them."""
    notes = [
        note
        for note in plan.data_quality.notes
        if not note.startswith(CHECK_PREFIX) and not note.startswith(CHANGE_PREFIX)
    ]
    told = [note for note in plan.data_quality.notes if note.startswith(CHANGE_PREFIX)]
    told.extend(f"{CHANGE_PREFIX}{change}" for change in changes)
    latest: list[str] = []
    for note in reversed(told):  # a repeated change counts at its latest telling
        if note not in latest:
            latest.append(note)
    notes.extend(reversed(latest[:MAX_CHANGE_NOTES]))
    written = 0
    for issue in issues:
        note = f"{CHECK_PREFIX}{issue.message}"
        if note in notes or written >= MAX_CHECK_NOTES:
            continue
        notes.append(note)
        written += 1
    plan.data_quality.notes = notes


# --- helpers -----------------------------------------------------------------


def _context(
    plan: TripPlan, context: TripContext | None, limits: RefineLimits | None, keep_ids: Any
) -> _Context:
    day_ends: dict[date, int] = {}
    ticket = selected_ticket(plan.tickets)
    if ticket is not None:
        for day_date, window in flight_windows([ticket.model_dump()], [day.date for day in plan.days]).items():
            if window.end is not None:
                day_ends[day_date] = window.end
    return _Context(
        buffer=PACE_BUFFER_MINUTES.get(_pace(context) if context is not None else "balanced", 5),
        protected=[*_required_names(context), *_must_see(context)] if context is not None else [],
        keep_ids=set(keep_ids or ()),
        diet=_diet(context) if context is not None else None,
        avoid=_avoid_terms(context),
        limits=limits or RefineLimits(),
        day_ends=day_ends,
    )


def _avoid_terms(context: TripContext | None) -> list[str]:
    if context is None:
        return []
    terms: list[str] = []
    for text in [*context.preferences, *context.hard_constraints]:
        for match in _AVOID.finditer(str(text)):
            word = match.group(1).lower()
            if word not in _AVOID_NOISE and _singular(word) not in _AVOID_NOISE:
                terms.append(_singular(word))
    return list(dict.fromkeys(terms))


def _labelled_for_diet(place: PlanItem | PlanAlternative, diet: str) -> bool:
    """The planner's own diet test (``_serves_diet``) on the source's types, name, cuisine and description."""
    row = {
        "name": place.name,
        "category": place.cuisine or "",
        "tags": list(place.source_types),
        "description": place.source_summary,
    }
    return _serves_diet(row, diet)


def _conflict(place: PlanItem | PlanAlternative, meal: bool, ctx: _Context) -> str:
    """Why a stop clashes with the stated diet or avoid wishes, or "" (only on the source's own words).

    A meal is judged on the diet only when the source's own types are on it, as compose had them,
    and flagged only when compose would have refused it: no type, name, cuisine or description
    labels it for the diet. A meal without them (a plan stored before they were kept) is not judged.
    """
    name, cuisine = place.name, place.cuisine
    tags = list(getattr(place, "tags", []) or [])
    if meal and ctx.diet and place.source_types and not _labelled_for_diet(place, ctx.diet):
        listed = f"is listed by its source as {cuisine}" if cuisine else "is listed by its source"
        return f"{listed}, with no {ctx.diet} label"
    if ctx.avoid:
        words = {_singular(word) for word in re.findall(r"[a-z][a-z\-]+", f"{name} {cuisine or ''} {' '.join(tags)}".lower())}
        hit = next((term for term in ctx.avoid if term in words), None)
        if hit:
            return f"matches \"{hit}\", which the trip asks to avoid"
    return ""


def _protected(item: PlanItem, ctx: _Context) -> bool:
    return (
        item.locked
        or item.confirmed
        or item.id in ctx.keep_ids
        or bool(ctx.protected and _is_must_see(item.name, ctx.protected))
    )


def _value(item: PlanItem) -> float:
    return (item.rating or 0.0) * math.log10((item.review_count or 0) + 1)


def _settle_transfers(day: DayPlan, touched: set[str]) -> None:
    """Clear a transfer that no longer starts from the stop before it (or whose ends were swapped)."""
    ordered = _ordered(day)
    for position, item in enumerate(ordered):
        transfer = item.transfer
        if transfer is None:
            continue
        previous = ordered[position - 1] if position else None
        if previous is None or transfer.from_item_id != previous.id or item.id in touched or previous.id in touched:
            item.transfer = None
            item.notes = with_transfer_note(item.notes, None, meal=item.type is ItemType.MEAL)


def _leg(previous: PlanItem, item: PlanItem) -> tuple[int, str, float] | None:
    """Minutes, mode and km from ``previous`` to ``item``: the item's transfer, else the transport model."""
    transfer = item.transfer
    km = _km(previous, item) if previous.geo is not None and item.geo is not None else None
    if transfer is not None and transfer.from_item_id == previous.id:
        distance = transfer.distance_km if transfer.distance_km is not None else (km or 0.0)
        return transfer.minutes + transfer.delay_minutes, transfer.mode, distance
    if km is None:
        return None
    mode, minutes = model_travel(km)
    return minutes, mode, km


def _walking_km(ordered: list[PlanItem]) -> float:
    total = 0.0
    for previous, item in zip(ordered, ordered[1:]):
        leg = _leg(previous, item)
        if leg is not None and leg[1] == "walk":
            total += leg[2]
    return total


def _km(first: PlanItem, second: PlanItem) -> float:
    return haversine_km(first.geo.lat, first.geo.lng, second.geo.lat, second.geo.lng)  # type: ignore[union-attr]


def _path_km(items: list[PlanItem]) -> float:
    points = [item for item in items if item.geo is not None]
    return sum(_km(first, second) for first, second in zip(points, points[1:]))


def _ordered(day: DayPlan) -> list[PlanItem]:
    return sorted(day.items, key=lambda item: (item.start, item.end))


def _overlaps(ordered: list[PlanItem]) -> bool:
    return any(current.start < previous.end for previous, current in zip(ordered, ordered[1:]))


def _fits(windows: list[tuple[int, int]], start: int, end: int) -> bool:
    return any(opens <= start and end <= closes for opens, closes in windows)


def _day_end(day_date: date, ctx: _Context) -> int:
    return min(DAY_LAST_END, ctx.day_ends.get(day_date, DAY_LAST_END))


def _times(items: list[PlanItem]) -> list[tuple[int, int]]:
    return [(_minutes(item.start), _minutes(item.end)) for item in items]


def _set_times(items: list[PlanItem], times: list[tuple[int, int]]) -> None:
    for item, (start, end) in zip(items, times):
        item.start, item.end = _hhmm(start), _hhmm(end)


def _day_name(day_date: date) -> str:
    return f"{_WEEKDAY[day_date.weekday()]} {day_date.isoformat()}"


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _round5(minutes: int) -> int:
    return -(-minutes // 5) * 5


__all__ = [
    "PlanIssue",
    "RefineLimits",
    "evaluate_plan",
    "refine_and_refresh",
    "refine_plan",
]
