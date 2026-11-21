"""The chat sentence after an edit is the model's. A JSON dump is not a sentence."""

import asyncio
from types import SimpleNamespace

from app.application.edit_reply import speak_edit, usable_reply


def test_a_sentence_is_kept_and_json_is_not() -> None:
    assert usable_reply("Day 1 already runs until 23:25, so I left it as it is.")
    assert usable_reply("{}") == ""
    assert usable_reply('{"answer": "hi"}') == ""
    assert usable_reply("```json\n{}\n```") == ""
    assert usable_reply("") == ""


def test_the_reply_call_sees_the_plan_and_a_longer_budget() -> None:
    seen: dict = {}

    class Gateway:
        available = True

        async def text(self, prompt, *, user, purpose, temperature, max_tokens, reasoning):
            seen["prompt"] = prompt
            seen["user"] = user
            seen["purpose"] = purpose
            seen["temperature"] = temperature
            seen["max_tokens"] = max_tokens
            seen["reasoning"] = reasoning
            return "Day 1 is already full, and no Gundam stop came back, so I left that day as it is."

    item = SimpleNamespace(name="FABLE", start="19:20", end="20:35", type=SimpleNamespace(value="meal"))
    plan = SimpleNamespace(days=[SimpleNamespace(date="2027-03-01", items=[item])])
    spoken = asyncio.run(
        speak_edit(
            Gateway(),
            message="can you add some gundam related attraction in hong kong, on day 1",
            history=(),
            plan=plan,
            facts="Heads-up: the place search did not return a located gundam attraction, so it was not added.",
        )
    )

    assert spoken.startswith("Day 1 is already full")
    assert seen["prompt"] == "reply"
    assert seen["purpose"] == "reply.edit"
    assert seen["temperature"] == 0.7
    assert seen["max_tokens"] == 4096
    assert seen["reasoning"] == "low"
    assert "FABLE" in seen["user"]
    assert "gundam" in seen["user"].lower()


def test_a_failed_reply_call_leaves_the_factual_sentence() -> None:
    class Gateway:
        available = True

        async def text(self, prompt, *, user, purpose, temperature, max_tokens, reasoning):
            raise RuntimeError("writer down")

    spoken = asyncio.run(
        speak_edit(Gateway(), message="add a museum", history=(), plan=SimpleNamespace(days=[]), facts="Added a museum.")
    )
    assert spoken == ""
