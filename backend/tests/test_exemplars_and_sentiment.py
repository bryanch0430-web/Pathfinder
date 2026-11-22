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


def test_keyword_handoff_uses_negative_words_and_extra_examples() -> None:
    from app.application.escalation import agent_reason, is_stuck, should_escalate
    from app.application.sentiment import frustration_from_jev

    policy = {"enabled": True, "match_mode": "keywords", "examples": [{"utterance": "ocean park"}]}
    assert should_escalate("this plan is useless", 0, policy) == "negative_word"
    assert should_escalate("please add ocean park tomorrow", 0, policy) == "keyword"
    assert should_escalate("plan three days in Kyoto", 0, policy) is None
    listed = {**policy, "words": ["delay"]}
    assert should_escalate("there is a delay", 0, listed) == "negative_word"
    assert should_escalate("this plan is useless", 0, listed) is None
    assert frustration_from_jev(0) == 100
    assert frustration_from_jev(4) == 0
    assert frustration_from_jev(2) == 50

    class Line:
        def __init__(self, role: str, content: str) -> None:
            self.role = role
            self.content = content

    messages = []
    for _ in range(3):
        messages.append(Line("user", "move the museum to the morning"))
        messages.append(Line("assistant", "Nothing in the plan matched that request, so it is unchanged."))
    assert is_stuck(messages, 3) is True
    assert is_stuck(messages[:2], 3) is False
    stayed = []
    for _ in range(3):
        stayed.append(Line("user", "remove fundam ok?"))
        stayed.append(Line("assistant", "No unlocked stop matched that removal, so the itinerary stayed as it was."))
    assert is_stuck(stayed, 3) is True
    done = [
        Line("user", "remove fundam ok?"),
        Line("assistant", "Replaced Benjamin Franklin House. Everything else is unchanged."),
    ] * 3
    assert is_stuck(done, 3) is False
    from app.application.escalation import stuck_flag

    gundam = []
    reply = "Updated to version 10. No itinerary items needed to change. Everything else is unchanged."
    assert stuck_flag([], reply) == 0 or stuck_flag([Line("user", "add gundam on day 1")], reply) == 1
    for number in (1, 2, 3):
        gundam.append(Line("user", "i mean, add an attraction at day 1 please, it should be somewhere i can see Gundam"))
        assert stuck_flag(gundam, reply) == number
        gundam.append(Line("assistant", reply))
    assert is_stuck(gundam, 3) is True
    assert agent_reason(60, False) == "hot"
    assert agent_reason(15, True) == "stuck"
    assert agent_reason(15, False) is None


def test_each_monitor_uses_its_own_lists() -> None:
    from app.application.escalation import hand_reason, is_stuck, line_is_hot

    class Line:
        def __init__(self, role: str, content: str) -> None:
            self.role = role
            self.content = content

    hand = {"enabled": True, "engine": "keywords", "words": ["refund"], "examples": ["talk to a manager"], "score": None}
    sentiment = {"enabled": True, "engine": "keywords", "words": ["furious"], "examples": ["this ruined my day"], "score": 60}
    assert hand_reason("I want a refund", 0, hand) == "negative_word"
    assert hand_reason("I want to talk to a live agent", 0, {**hand, "words": [], "examples": []}) == "asked_for_a_person"
    assert hand_reason("please find someone to help", 0, {**hand, "words": [], "examples": []}) == "asked_for_a_person"
    assert hand_reason("please don't talk to a live agent", 0, hand) is None
    assert hand_reason("I am furious", 0, hand) is None
    assert line_is_hot("I am furious", 0, sentiment) is True
    assert line_is_hot("I want a refund", 0, sentiment) is False
    mild = {"enabled": True, "engine": "keywords", "words": [{"text": "slow", "score": 40}], "examples": [], "score": 60}
    sharp = {"enabled": True, "engine": "keywords", "words": [{"text": "slow", "score": 80}], "examples": [], "score": 60}
    assert hand_reason("this is slow", 0, mild) == "negative_word"
    assert line_is_hot("this is slow", 0, mild) is False
    assert line_is_hot("this is slow", 0, sharp) is True
    stuck_lines = []
    for _ in range(3):
        stuck_lines.append(Line("user", "remove fundam ok?"))
        stuck_lines.append(Line("assistant", "No stop matched that removal, so nothing moved."))
    assert is_stuck(stuck_lines, 3, phrases=["nothing moved"], examples=[], engine="phrases") is True
    assert is_stuck(stuck_lines, 3, phrases=["priced wrong"], examples=[], engine="phrases") is False


def test_a_dates_form_is_not_handed_to_a_person() -> None:
    from app.application.escalation import queue_reason

    assert queue_reason("Here are the missing details — dates: This weekend.", hot=False, stuck=True) is None


def test_jev_sentiment_sends_the_word_list_and_the_extra_examples() -> None:
    from app.infrastructure.decision import sentiment_payload

    payload = sentiment_payload("this is useless", ["useless", "delay"], ["ocean park"])
    assert payload["state"]["negative_words"] == [{"text": "useless", "score": 60}, {"text": "delay", "score": 60}]
    assert payload["state"]["extra_examples"] == [{"text": "ocean park", "score": 60}]
    assert "score" in payload["questions"]["sentiment"]["instructions"]
    assert payload["questions"]["sentiment"]["criteria"][0].startswith("Hostile")
