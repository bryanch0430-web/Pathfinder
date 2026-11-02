"""Identity answers: honest, instant, and never swallowing a travel request."""

from __future__ import annotations

from app.application.persona import identity_reply
from app.application.prompts import PromptLibrary
from app.config import Settings


def test_who_are_you_is_honest_about_being_software() -> None:
    reply = identity_reply("who are you?")

    assert reply is not None
    assert "Pathfinder" in reply
    assert "software" in reply.lower() or "ai" in reply.lower()
    assert "not a person" in reply.lower() or "not a real person" in reply.lower()


def test_are_you_a_real_person_says_no() -> None:
    reply = identity_reply("are you a real person")

    assert reply is not None
    assert reply.lower().lstrip().startswith("no")


def test_what_can_you_do_describes_the_product() -> None:
    reply = identity_reply("what can you do?")

    assert reply is not None
    assert "itinerary" in reply.lower()
    assert "source" in reply.lower()


def test_model_question_does_not_pretend_to_be_a_model_vendor() -> None:
    reply = identity_reply("which model are you?")

    assert reply is not None
    assert "language model" in reply.lower()


def test_travel_requests_are_not_caught() -> None:
    for message in (
        "plan a trip to kyoto",
        "who is the chef at amber",
        "what is on day 1?",
        "is ocean park worth it?",
    ):
        assert identity_reply(message) is None, message


def test_every_writer_prompt_carries_the_persona(settings: Settings) -> None:
    system = PromptLibrary(settings).system("ask")

    assert "Pathfinder" in system
    assert "not a person" in system.lower()
