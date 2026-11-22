"""The three demo bugs from the 6 November review."""

import asyncio
from datetime import date, datetime

from app.application.llm_gateway import LLMGateway
from app.application.orchestrator import _fill_origin_answer, _pending_clarify
from app.application.prompts import PromptLibrary
from app.application.trip_rules import days_before_landing, flight_windows, landing_date
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


def test_one_date_and_an_unsaid_party_stay_missing() -> None:
    from app.application.understanding import _normalise_dates

    context = TripContext(session_id="s", destination="Kyoto", start_date=date(2026, 11, 30))
    _normalise_dates(context)
    assert context.days is None
    assert context.missing_slots() == ["origin", "dates", "party_size", "budget"]


def test_seeing_gundam_is_a_wish_not_a_city(settings: Settings) -> None:
    context = TripContext(session_id="s")
    updated = asyncio.run(
        understand(
            "hi, i want to plan a trip that can see Gundam",
            context,
            LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings),
        )
    )
    assert "must-see: Gundam" in updated.preferences
    assert updated.destination != "Gundam"
    assert updated.origin == ""


def test_lowercase_gundam_is_a_wish_and_not_the_departure_city() -> None:
    from app.application.understanding import PlaceGrounding, understand_note
    class Gateway:
        available = True

        async def json(self, prompt, *, user, purpose):
            assert "must-see: Gundam" in user
            return {"destination": "Japan", "origin": "Japan"}

    context = TripContext(session_id="s")
    updated = asyncio.run(understand("I want to go japan to see gundam", context, Gateway()))
    assert updated.destination == "Japan"
    assert updated.origin == ""
    assert "must-see: Gundam" in updated.preferences
    note = understand_note(
        context,
        updated,
        PlaceGrounding(suggestions=[("destination", "Japan", "Japan")]),
    )
    assert note.startswith("Destination Japan. Wish must-see: Gundam. Confirm Japan as Japan")
    edit = understand_note(context, context, message="add a sakura flower stop on day 2 morning ok?")
    assert edit.startswith("Add sakura flower on day 2 in the morning")
    assert "Still missing" in note


def test_a_question_with_no_plan_is_answered_from_a_lookup(settings: Settings) -> None:
    from types import SimpleNamespace

    from app.application.asking import AskPath
    from app.application.toolbox import Toolbox
    from app.infrastructure.providers.websearch import WebResult

    class Web:
        name = "fake-web"

        async def search(self, *, query: str, count: int = 4, ori_question: str | None = None):
            return [
                WebResult(
                    title="Gundam",
                    url="https://example.com/gundam",
                    summary="Gundam is a Japanese media franchise of giant robot stories.",
                )
            ]

    path = AskPath(
        gateway=LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings),
        registry=SimpleNamespace(web=[Web()], web_deep=[], weather=[], routes=[], places=[]),
        settings=settings,
    )
    context = TripContext(
        session_id="s",
        destination="Kyoto",
        start_date=date(2026, 11, 30),
        budget=8300,
        preferences=["must-see: Gundam"],
    )

    async def ask() -> str:
        text, _related, _source = await path.answer(
            question="what is gundam from japan",
            plan=None,
            context=context,
            toolbox=Toolbox(path="ask", tracer=NoopTracer(), store=None, request_id="r"),
            emit=None,
        )
        return text

    text = asyncio.run(ask())
    assert "giant robot" in text
    assert "nothing to answer from" not in text
    assert "Where will you" not in text


def test_a_correction_is_not_a_departure_city() -> None:
    context = TripContext(session_id="s", destination="Gunda", party_size=None)
    assert _fill_origin_answer(context, ["origin", "dates"], "nonono, i mean, Gundam") is False
    assert context.origin == ""


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


def test_a_day_before_the_flight_lands_is_not_in_the_city() -> None:
    dates = [date(2027, 3, 10), date(2027, 3, 11), date(2027, 3, 12)]
    overnight = [{"arrive_at": "2027-03-11T01:30:00", "return_depart_at": "2027-03-14T18:00:00"}]
    assert landing_date(overnight) == date(2027, 3, 11)
    assert days_before_landing(overnight, dates) == {date(2027, 3, 10)}
    assert days_before_landing([{"arrive_at": "2027-03-10T09:00:00"}], dates) == set()
    assert days_before_landing([], dates) == set()
