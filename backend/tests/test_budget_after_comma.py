"""A budget typed right after a comma is read, and a long run of digits and commas still costs time in proportion to it.

The pattern for "<amount> <currency code>" must not start a number in the middle of another one ("1,200.75 SGD"
is 1,200.75, not 200.75), which it does by looking at what comes before the first digit. Looking at "a digit or
a comma" lost "3 days,500 USD" too, where the comma only separates two parts of the sentence.
"""

from __future__ import annotations

import time
from datetime import date

import pytest

from app.application.understanding import _apply_rules
from app.domain.models import TripContext

TODAY = date(2026, 11, 3)
BUDGET = 2.0  # CPU seconds


def _read(text: str) -> tuple[float | None, str]:
    context = TripContext(session_id="s")
    _apply_rules(text, context, TODAY)
    return context.budget, context.currency


@pytest.mark.parametrize(
    ("text", "amount", "currency"),
    [
        ("Plan Kyoto, 3 days,500 USD", 500.0, "USD"),
        ("3 days in Kyoto,500 USD", 500.0, "USD"),
        ("for 2 people,3000 HKD", 3000.0, "HKD"),
        ("2 people,1500 USD, 4 days", 1500.0, "USD"),
        ("Kyoto,1,500 USD", 1500.0, "USD"),
        ("4 days;500 EUR", 500.0, "EUR"),
        ("4 days,500.50 GBP", 500.5, "GBP"),
    ],
)
def test_a_budget_glued_to_a_comma_is_read(text: str, amount: float, currency: str) -> None:
    assert _read(text) == (amount, currency)


@pytest.mark.parametrize(
    ("text", "amount", "currency"),
    [
        ("max 1,200.75 SGD", 1200.75, "SGD"),
        ("we can spend 5000 HKD", 5000.0, "HKD"),
        ("1,2000 USD", 12000.0, "USD"),
        ("about 70 usd each", 70.0, "USD"),
        ("2 people, 1500 USD, 4 days", 1500.0, "USD"),
        ("1500USD!", 1500.0, "USD"),
    ],
)
def test_a_number_is_still_read_from_its_first_digit(text: str, amount: float, currency: str) -> None:
    assert _read(text) == (amount, currency)


@pytest.mark.parametrize(
    "text",
    [
        "1" * 12_000,
        "1," * 6_000,
        "1 " * 6_000,
        "1." * 6_000,
        "," + "1," * 6_000,
        "x," + "12," * 4_000,
        "1,2 " * 3_000,
        "a,1 " * 3_000,
        "1,500," * 2_000 + " USD",
        "," * 12_000,
    ],
    ids=["digits", "digits and commas", "digits and blanks", "digits and dots", "commas first", "words and commas",
         "pairs", "letters commas digits", "amounts and a code", "commas"],
)
def test_hostile_runs_of_digits_and_commas_take_time_in_proportion_to_them(text: str) -> None:
    started = time.process_time()

    _read(text)

    assert time.process_time() - started < BUDGET
