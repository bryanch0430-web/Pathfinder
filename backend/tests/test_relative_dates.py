from __future__ import annotations

from datetime import date

from app.core.dates import relative_window_label
from app.domain.models import RoutePayload, TripContext
from app.understand.pipeline import missing_for_plan
from app.turn.orchestrator import _fallback_clarify


def test_next_month_is_a_window_not_an_invented_day():
    assert relative_window_label("2-day Taipei trip next month", date(2026, 11, 23)) == "December 2026"


def test_known_length_is_not_asked_again_and_month_is_kept():
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
    clarify = _fallback_clarify(context, RoutePayload(intent="plan", date_window="December 2026"))
    assert clarify.slots == ["start_date"]
    assert "December 2026" in clarify.question
    assert "2-day" in clarify.question


def test_date_question_does_not_add_a_currency_field():
    from app.understand.pipeline import _clarify
    from app.understand.schemas import ParsedIntent

    context = TripContext(session_id="dates-test", destination="Taipei", days=2, party_size=1, currency="TWD")
    parsed = ParsedIntent(
        intent="plan",
        confidence=0.9,
        missing_slots=["start_date"],
        question="Which dates in December would you like for your 2-day Taipei trip, budget 12,000 TWD?",
    )
    clarify = _clarify(parsed, context, None, RoutePayload(intent="plan", need_clarification=True, date_window="December 2026"))
    assert clarify.slots == ["start_date"]
