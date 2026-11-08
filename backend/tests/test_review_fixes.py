"""The three demo bugs from the 6 November review."""

import asyncio
from datetime import date, datetime

from app.application.llm_gateway import LLMGateway
from app.application.orchestrator import _fill_origin_answer, _pending_clarify
from app.application.prompts import PromptLibrary
from app.application.trip_rules import flight_windows
from app.application.understanding import understand
from app.config import Settings
from app.domain.models import Message, TripContext
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer


def test_a_short_origin_answer_does_not_replace_the_destination(settings: Settings) -> None:
    context = TripContext(
        session_id="s",
        destination="Osaka",
        start_date=date(2027, 4, 1),
        end_date=date(2027, 4, 3),
        party_size=2,
        budget=15000,
    )
    history = [
        Message(session_id="s", role="user", content="Plan a 3-day trip to Osaka for 2 people in April"),
        Message(
            session_id="s",
            role="assistant",
            content="Where are you travelling from?",
            meta={"clarify": True, "slots": ["origin"]},
        ),
        Message(session_id="s", role="user", content="Hong Kong"),
    ]
    assert _pending_clarify(history) == ["origin"]
    assert _fill_origin_answer(context, ["origin"], "Hong Kong") is True

    updated = asyncio.run(understand("Hong Kong", context, LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)))
    updated.destination = "Osaka"
    updated.start_date = context.start_date
    updated.end_date = context.end_date

    assert updated.origin == "Hong Kong"
    assert updated.destination == "Osaka"


def test_a_new_trip_sentence_is_not_an_origin_answer() -> None:
    context = TripContext(session_id="s", destination="Osaka", party_size=2)
    assert _fill_origin_answer(context, ["origin"], "Plan a 3-day trip to Hong Kong") is False
    assert context.origin == ""


def test_flight_arrival_opens_the_first_day_and_the_return_closes_the_last() -> None:
    dates = [date(2026, 12, 1), date(2026, 12, 2), date(2026, 12, 3)]
    windows = flight_windows(
        [
            {
                "arrive_at": datetime(2026, 12, 1, 22, 45),
                "return_depart_at": datetime(2026, 12, 3, 6, 35),
            }
        ],
        dates,
    )
    assert windows[dates[0]].start == 22 * 60 + 45 + 120
    assert windows[dates[2]].end == 6 * 60 + 35 - 210
