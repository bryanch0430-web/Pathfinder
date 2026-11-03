"""Chat date ranges: "18 to 23 November" is the 18th to the 23rd, not a trip starting on the 23rd."""

import asyncio
from datetime import date

import pytest

from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.understanding import _find_dates, understand
from app.config import Settings
from app.domain.models import TripContext
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer

TODAY = date(2026, 11, 3)
NOV_18_23 = (date(2026, 11, 18), date(2026, 11, 23))


@pytest.mark.parametrize(
    "text",
    [
        "18 to 23 november 2026",
        "18-23 november",
        "18–23 nov",
        "november 18 to 23",
        "nov 18-23, 2026",
        "18 nov to 23 nov",
        "from 18 november to 23 november",
        "nov 18 - nov 23, 2026",
        "18th to 23rd november",
        "between 18 and 23 november",
        "18 nov until 23 nov",
    ],
)
def test_a_date_range_gives_both_ends(text: str) -> None:
    assert _find_dates(text, TODAY) == NOV_18_23


def test_a_range_across_new_year_ends_next_year() -> None:
    assert _find_dates("28 december to 2 january", TODAY) == (date(2026, 12, 28), date(2027, 1, 2))
    assert _find_dates("dec 28 - jan 2, 2027", TODAY) == (date(2026, 12, 28), date(2027, 1, 2))


def test_an_explicit_year_is_used() -> None:
    assert _find_dates("18 to 23 november 2027", TODAY) == (date(2027, 11, 18), date(2027, 11, 23))


def test_single_dates_and_older_forms_still_work() -> None:
    assert _find_dates("18 november", TODAY) == (date(2026, 11, 18), None)
    assert _find_dates("nov 18", TODAY) == (date(2026, 11, 18), None)
    assert _find_dates("1 march", TODAY) == (date(2027, 3, 1), None)  # past this year → next year
    assert _find_dates("in december", TODAY) == (date(2026, 12, 1), None)
    assert _find_dates("2026-11-18 to 2026-11-23", TODAY) == NOV_18_23


def test_a_constraint_date_is_not_taken_as_the_trip_end() -> None:
    text = "from 18 november, 6 days. 20 november cannot have any plan"
    assert _find_dates(text, TODAY) == (date(2026, 11, 18), None)


def test_numbers_that_are_not_dates_are_ignored() -> None:
    assert _find_dates("2 to 3 people, budget 10000", TODAY) == (None, None)


def test_the_reported_message_plans_18_to_23_november(settings: Settings) -> None:
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    message = "Plan a 6-day trip to Korea from Hong Kong, 18 to 23 November 2026, 2 people, budget HKD 10000."
    updated = asyncio.run(understand(message, TripContext(session_id="s"), gateway, today=TODAY))
    assert (updated.start_date, updated.end_date) == NOV_18_23
    assert updated.days == 6
