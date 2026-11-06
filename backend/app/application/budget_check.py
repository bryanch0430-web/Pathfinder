"""The check before the agents run on a new trip, and the budget options of the clarify card.

A budget of zero, or one below a floor per traveller per night (``min_budget_per_person_night``, in US dollars and
converted to the trip's currency), cannot pay for the trip: the hotel search would drop every stay and the plan would
come back with none. So the traveller is asked first, with a budget that would work and "plan anyway" as the options.
A trip whose dates have all gone by is asked about the same way. What the traveller chose to keep is marked on the trip
(``TripContext.budget_confirmed`` and ``past_dates_confirmed``), and the check does not ask again.

Nothing here calls a model or a source: it reads the trip form and the exchange table only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.config import Settings
from app.domain.models import TripContext

#: The currency ``min_budget_per_person_night`` is written in: the base of the exchange table.
FLOOR_CURRENCY = "USD"
#: The suggested budget is this much above the floor for the trip.
SUGGESTED_FACTOR = 1.5
#: The clarify card's budget options, as multiples of the floor for the trip.
OPTION_FACTORS = (1.0, 2.0, 3.5)
#: The nights a trip without dates is sized for (a three-day trip).
DEFAULT_NIGHTS = 2
#: The clarify card's options when the floor is turned off: the fixed amounts the card always had.
FIXED_OPTIONS = (1000, 2500, 5000)
BUDGET_REASON = "budget_below_floor"
PAST_DATES_REASON = "start_date_past"
#: The last sentence of the budget question and the opening of the dates question: a reply to the turn that carries
#: one of them answers that question (see ``asked_about``).
BUDGET_QUESTION_TAIL = "Raise the budget, shorten the trip, or plan anyway with an over-budget hotel?"
PAST_DATES_LEAD = "The start date is in the past ("


@dataclass
class PrePlanQuestion:
    """A question asked instead of planning, sent like any clarify question."""

    question: str
    slots: list[str]
    options: dict[str, list[str]] = field(default_factory=dict)
    reason_code: str = ""


def floor_per_person_night(settings: Settings, fx: Any, currency: str) -> float:
    """The floor in the trip's currency; 0 when the check is off. Without an exchange rate for the currency the
    check is off too: the dollar amount read as another currency would be no floor at all, or far too high."""
    amount = float(settings.min_budget_per_person_night or 0)
    target = (currency or FLOOR_CURRENCY).upper()
    if amount <= 0:
        return 0.0
    if target == FLOOR_CURRENCY:
        return amount
    has_rate = getattr(fx, "has_rate", None)
    if has_rate is None or not has_rate(FLOOR_CURRENCY) or not has_rate(target):
        return 0.0
    return float(fx.convert(amount, FLOOR_CURRENCY, target))


def trip_nights(context: TripContext) -> int:
    """The nights of the trip the form holds, at least one; DEFAULT_NIGHTS when it has no dates yet."""
    try:
        dates = context.date_range()
    except ValueError:  # an end before the start, or more than 60 days: the form's own checks report it
        dates = None
    if dates is not None:
        return max(len(dates.dates) - 1, 1)
    if context.days:
        return max(context.days - 1, 1)
    return DEFAULT_NIGHTS


def travellers(context: TripContext) -> int:
    return context.party_size or 1


def nice_amount(amount: float) -> int:
    """An amount rounded UP to two significant figures: 42,120 is 43,000 and 2,340 is 2,400. Up, so a suggested
    budget is never below the floor it was sized from (rounded down, the first option asked the question again)."""
    if amount <= 0:
        return 0
    step = 10 ** max(int(math.floor(math.log10(amount))) - 1, 0)
    # A hair of float error (2,300.0000001) must not lift an exact amount to the next step.
    return int(math.ceil(round(amount / step, 6)) * step)


def money(currency: str, amount: float) -> str:
    return f"{(currency or FLOOR_CURRENCY).upper()} {amount:,.0f}"


def budget_options(context: TripContext, floor: float) -> list[str]:
    """Three budgets sized to the trip: the floor for its nights and travellers, twice it, and three and a half times."""
    currency = (context.currency or FLOOR_CURRENCY).upper()
    base = floor * trip_nights(context) * travellers(context)
    if base <= 0:
        return [f"{amount} {currency}" for amount in FIXED_OPTIONS]
    return [money(currency, nice_amount(base * factor)) for factor in OPTION_FACTORS]


def budget_question(context: TripContext, *, floor: float, today: date) -> PrePlanQuestion | None:
    """The question to ask before planning this trip, or None when it can be planned as it is."""
    past = _past_dates_question(context, today)
    if past is not None:
        return past
    if context.budget is None or context.budget_confirmed:
        return None
    if context.budget > 0 and floor <= 0:
        # The check is off (or the currency has no rate): only a budget of nothing is asked about.
        return None
    nights, party = trip_nights(context), travellers(context)
    per_night = context.budget / (nights * party)
    if context.budget > 0 and per_night >= floor:
        return None
    currency = (context.currency or FLOOR_CURRENCY).upper()
    if floor > 0:
        suggested = money(currency, nice_amount(floor * nights * party * SUGGESTED_FACTOR))
    else:
        # No floor to size a budget from: the middle of the card's fixed amounts.
        suggested = money(currency, FIXED_OPTIONS[1])
    question = (
        f"{money(currency, context.budget)} for {party} {'person' if party == 1 else 'people'} over {nights} "
        f"night{'s' if nights != 1 else ''} is about {money(currency, per_night)} a night per person. "
        + BUDGET_QUESTION_TAIL
    )
    return PrePlanQuestion(
        question=question,
        slots=["budget"],
        options={"budget": [suggested, f"Keep {money(currency, context.budget)} and plan anyway"]},
        reason_code=BUDGET_REASON,
    )


def _past_dates_question(context: TripContext, today: date) -> PrePlanQuestion | None:
    """Asked only when the whole trip has gone by: a trip under way (started, not yet ended) is planned as it is."""
    start = context.start_date
    if start is None or start >= today or context.past_dates_confirmed:
        return None
    try:
        dates = context.date_range()
    except ValueError:
        dates = None
    length = (dates.end - dates.start) if dates is not None else timedelta(days=max((context.days or 1) - 1, 0))
    if start + length >= today:
        return None
    following = _same_day_after(start, today)
    suggestion = f"{following.isoformat()} to {(following + length).isoformat()}"
    return PrePlanQuestion(
        question=(
            f"{PAST_DATES_LEAD}{_written(start)}). Did you mean {_written(following)}? "
            "Pick that, give other dates, or keep these dates and plan anyway."
        ),
        slots=["dates"],
        options={"dates": [suggestion, "Keep these dates and plan anyway"]},
        reason_code=PAST_DATES_REASON,
    )


def asked_about(assistant_text: str) -> str | None:
    """Which check-before-planning question an assistant turn asked: "budget", "dates", or None."""
    text = assistant_text or ""
    if BUDGET_QUESTION_TAIL in text:
        return "budget"
    if text.startswith(PAST_DATES_LEAD):
        return "dates"
    return None


def _same_day_after(start: date, today: date) -> date:
    """The same day and month in the first later year that is not before today (29 February becomes the 28th)."""
    year = start.year + 1
    while True:
        try:
            candidate = start.replace(year=year)
        except ValueError:
            candidate = date(year, start.month, 28)
        if candidate >= today:
            return candidate
        year += 1


def _written(day: date) -> str:
    return f"{day.day} {day:%B %Y}"
