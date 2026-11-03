"""Reasoning text is kept for the trace and never returned as the answer."""

from app.infrastructure.llm import OpenAICompatibleLLM


def test_reasoning_is_separate_from_the_answer() -> None:
    message = {
        "content": "the answer",
        "reasoning_content": "the model considered two hotels",
    }
    assert OpenAICompatibleLLM._content(message) == "the answer"
    assert OpenAICompatibleLLM._reasoning(message) == "the model considered two hotels"


def test_empty_reasoning_is_blank_and_a_list_is_joined() -> None:
    assert OpenAICompatibleLLM._reasoning({"content": "ok"}) == ""
    assert OpenAICompatibleLLM._reasoning({"reasoning": [{"text": "one "}, {"text": "two"}]}) == "one two"
