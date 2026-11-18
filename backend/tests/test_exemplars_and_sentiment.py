"""How people actually talk to the planner."""

import asyncio
from datetime import datetime, timedelta

from app.application.exemplars import similarity
from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.router import RULES_FIRST, System1Router, not_about_the_trip
from app.application.sentiment import frustration_band, frustration_score, line_reading, session_frustration
from app.config import Settings
from app.domain.models import Intent, TripContext
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer
from app.infrastructure.store import SQLiteStore


def test_a_planned_trip_a_question_and_a_change_are_different_kinds_of_line() -> None:
    assert not_about_the_trip("Plan a 3-day trip to Hong Kong from Singapore") is False
    assert not_about_the_trip("What is the weather on day 2?") is False
    assert not_about_the_trip("Make it cheaper") is False
    assert not_about_the_trip("hello, how do you do?") is True
    assert not_about_the_trip("....fuck you") is True
    assert not_about_the_trip("the other one") is False


def test_two_unrelated_lines_barely_overlap() -> None:
    assert similarity("hello there", "plan a trip") < 0.5


def test_how_people_talk_and_a_taught_line_wins_before_the_regex(settings: Settings) -> None:
    """A trip, a question and a change are rules. Chat abstains. An insult asks for a clarification."""
    router = System1Router(
        gateway=LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings),
        settings=settings,
        use_model=True,
    )
    started = datetime.now().date()
    context = TripContext(
        session_id="s",
        destination="Kyoto",
        start_date=started + timedelta(days=30),
        end_date=started + timedelta(days=32),
        party_size=2,
        budget=400000,
        currency="JPY",
    )
    plan = {"trip_id": "t", "destination": "Kyoto"}

    async def decide(message: str):
        return await router.route(message=message, context=context, plan_summary=plan, history=[])

    weather = asyncio.run(decide("What is the weather on day 2?"))
    assert weather.intent == Intent.ASK
    assert weather.rationale == RULES_FIRST

    change = asyncio.run(decide("Make the trip cheaper"))
    assert change.intent == Intent.MODIFY
    assert change.rationale == RULES_FIRST

    hello = asyncio.run(decide("hello, how do you do?"))
    assert hello.intent == Intent.ASK
    assert hello.rationale != RULES_FIRST

    aside = asyncio.run(decide("why are you gay"))
    assert aside.intent == Intent.ASK
    assert aside.rationale != RULES_FIRST

    bare = asyncio.run(decide("....fuck you"))
    assert bare.intent == Intent.UNCLEAR
    assert bare.need_clarification is True


def test_a_held_chat_keeps_its_note(tmp_path) -> None:
    store = SQLiteStore(tmp_path / "learn.db")
    store.hold_chat("s")
    assert store.is_held("s") is True
    store.set_desk_note("s", "Call them back about the hotel.")
    store.release_chat("s")
    assert store.is_held("s") is False
    assert store.desk_note("s") == "Call them back about the hotel."


def test_frustration_rises_with_heat_and_stays_low_for_a_plain_request() -> None:
    assert frustration_score("Plan a 3-day trip to Hong Kong for 2") < 25
    assert frustration_score("hello, how do you do?") < 25
    rude = frustration_score("....fuck you")
    assert rude >= 25
    assert frustration_score("you are rubbish") >= 25
    assert frustration_score("THIS IS USELESS!!!!") > rude
    assert frustration_band(frustration_score("thanks")) == "calm"
    assert frustration_band(frustration_score("THIS IS USELESS!!!!")) == "hot"


def test_a_line_is_scored_on_a_signed_scale_and_silence_is_not_calm() -> None:
    blank = line_reading(" ")
    assert blank["score"] is None
    assert blank["label"] == "not_measured"
    flipped = line_reading("not useless")
    assert flipped["score"] is not None and flipped["score"] > 0
    assert "negation" in flipped["notes"]
    loud = line_reading("useless useless useless useless")
    once = line_reading("useless")
    assert loud["score"] is not None and once["score"] is not None
    assert loud["score"] < once["score"]
    assert "repeat" in loud["notes"]
    assert session_frustration([-0.4, -0.2]) is None
    assert session_frustration([-0.4, -0.2, -0.6]) == 40
