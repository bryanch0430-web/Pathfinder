"""The rules that read a chat message take time in proportion to the message.

A chat message may be 6,000 characters and the rules run on the one thread that serves every
traveller, so a message built to make one of their patterns backtrack (a long run of blanks before a
currency, a long run of digits before a currency code) stalls everybody for as long as it takes.
The sizes here are twice what a message may be, and the budget is generous: the work takes
milliseconds.
"""

from __future__ import annotations

import time
from datetime import date

import pytest

from app.application.understanding import _apply_rules
from app.domain.models import TripContext

BUDGET = 2.0  # CPU seconds
TODAY = date(2026, 11, 3)

HOSTILE = {
    "blanks": "<" + " " * 11_998 + "x",
    "blanks after the word budget": "budget" + " " * 11_990 + "x",
    "blanks after a currency sign": "$" + " " * 11_998 + "x",
    "digits": "1" * 12_000,
    "digits and commas": "1," * 6_000,
    "digits and blanks": "1 " * 6_000,
    "digits and dots": "1." * 6_000,
    "currency signs": "$1 " * 4_000,
    "the word budget": "budget " * 1_700,
}


@pytest.mark.parametrize("label", list(HOSTILE))
def test_the_rules_take_time_in_proportion_to_the_message(label: str) -> None:
    context = TripContext(session_id="s")
    started = time.process_time()

    _apply_rules(HOSTILE[label], context, TODAY)

    took = time.process_time() - started
    assert took < BUDGET, f"{took:.1f} s for {len(HOSTILE[label])} characters"


@pytest.mark.parametrize(
    ("text", "amount", "currency"),
    [
        ("Plan Kyoto, budget 400,000 JPY", 400000, "JPY"),
        ("Plan Kyoto with a budget of $400", 400, "USD"),
        ("under USD 300 please", 300, "USD"),
        ("around \u20ac1,500 in total", 1500, "EUR"),
        ("budget:HK$ 8,000", 8000, "HKD"),
        ("a budget   of   \u00a3 2500.50 all in", 2500.5, "GBP"),
        ("we can spend 5000 HKD", 5000, "HKD"),
        ("max 1,200.75 SGD", 1200.75, "SGD"),
        ("about 70 usd each", 70, "USD"),
    ],
)
def test_the_budget_is_still_read_as_before(text: str, amount: float, currency: str) -> None:
    context = TripContext(session_id="s")

    _apply_rules(text, context, TODAY)

    assert (context.budget, context.currency) == (amount, currency)
