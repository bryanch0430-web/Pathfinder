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
        budget=12000,
        currency_set=True,
        start_date=None,
        end_date=None,
    )
    assert missing_for_plan(context) == ["start_date"]
    clarify = _fallback_clarify(context, RoutePayload(intent="plan", date_window="December 2026"))
    assert clarify.slots == ["start_date"]
    assert "December 2026" in clarify.question
    assert "2-day" in clarify.question


def test_missing_origin_and_budget_are_asked_together():
    context = TripContext(
        session_id="dates-test",
        destination="Hong Kong",
        start_date="2027-03-12",
        days=3,
        party_size=2,
    )
    clarify = _fallback_clarify(context, RoutePayload(intent="plan"))
    assert clarify.slots == ["origin", "budget"]
    assert clarify.question == "I need these before I can plan."


def test_a_made_up_slot_name_is_not_shown_on_the_card():
    from app.understand.pipeline import _clarify
    from app.understand.schemas import ParsedIntent

    context = TripContext(
        session_id="note-test", destination="Hong Kong", origin="Singapore",
        start_date="2027-03-12", end_date="2027-03-14", days=3, party_size=2,
        budget=9000, currency="HKD", currency_set=True,
    )
    parsed = ParsedIntent(
        intent="modify",
        confidence=0.85,
        missing_slots=["note_text"],
        question="What should the note say?",
    )
    route = RoutePayload(intent="modify", need_clarification=True)
    clarify = _clarify(parsed, context, None, route)
    assert clarify.slots == []
    assert clarify.question == "What should the note say?"


def test_date_question_does_not_add_a_currency_field():
    from app.understand.pipeline import _clarify
    from app.understand.schemas import ParsedIntent

    context = TripContext(
        session_id="dates-test",
        destination="Taipei",
        origin="Hong Kong",
        days=2,
        party_size=1,
        budget=12000,
        currency="TWD",
        currency_set=True,
    )
    parsed = ParsedIntent(
        intent="plan",
        confidence=0.9,
        missing_slots=["start_date"],
        question="Which dates in December would you like for your 2-day Taipei trip, budget 12,000 TWD?",
    )
    clarify = _clarify(parsed, context, None, RoutePayload(intent="plan", need_clarification=True, date_window="December 2026"))
    assert clarify.slots == ["start_date"]
