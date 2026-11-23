from __future__ import annotations

from datetime import date

from app.core.dates import resolve_relative_start
from app.domain.models import TripContext
from app.understand.pipeline import missing_for_plan
from app.turn.orchestrator import _fallback_clarify


def test_next_month_fills_a_provisional_start_date():
    start, note = resolve_relative_start("2-day Taipei trip next month", date(2026, 11, 23))
    assert start == date(2026, 12, 1)
    assert note and "next month" in note


def test_known_length_is_not_asked_again():
    context = TripContext(
        session_id="dates-test",
        destination="Taipei",
        origin="Hong Kong",
        days=2,
        party_size=1,
        start_date=None,
        end_date=None,
    )
    assert missing_for_plan(context) == ["start_date"]
    clarify = _fallback_clarify(context, None)
    assert clarify.slots == ["start_date"]
    assert "2-day" in clarify.question
