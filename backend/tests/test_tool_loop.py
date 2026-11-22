"""A path may look something up, and a modify JSON is the action."""

import asyncio
from types import SimpleNamespace

from app.application.modification import ChangeDraft, interpret_plan, mark_model_draft, operation_for_edit
from app.application.modification import _shape_hint
from app.application.tool_loop import gather
from app.application.toolbox import Toolbox
from app.infrastructure.observability import NoopTracer
from tests.plan_samples import make_plan


class _Page:
    def __init__(self, title: str, snippet: str) -> None:
        self.title = title
        self.snippet = snippet


class _Web:
    available = True

    async def search(self, *, query: str, count: int = 4, ori_question: str | None = None):
        assert "cat cafe" in query
        return [_Page("Shibuya cat cafe", "a cafe with cats")]


class _Gateway:
    available = True

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def json(self, prompt, *, user, purpose):
        assert prompt == "tool-round"
        assert purpose == "tool.round"
        self.seen.append(user)
        if len(self.seen) == 1:
            return {"tool": "web.search", "query": "cat cafe Shibuya"}
        assert "Shibuya cat cafe" in user
        return {"tool": None}


def test_a_look_up_comes_back_to_the_model() -> None:
    gateway = _Gateway()
    steps: list[dict] = []
    notes = asyncio.run(
        gather(
            gateway,
            Toolbox(path="ask", tracer=NoopTracer(), store=None, request_id="t"),
            SimpleNamespace(web=[_Web()], places=[], weather=[], documents=None),
            message="what cat cafes are near day 2",
            context=SimpleNamespace(destination="Tokyo"),
            plan=None,
            on_step=steps.append,
        )
    )
    assert "Shibuya cat cafe" in notes
    assert len(gateway.seen) == 2
    assert [step["status"] for step in steps] == ["running", "done"]


def test_a_modify_json_is_the_action_and_the_sentence_is_not_scanned() -> None:
    draft = mark_model_draft(ChangeDraft(operation="add", place="cat cafe", confidence=0.4))
    plan = make_plan(days=1, with_tickets=False, with_stay=False, stops_per_day=0)
    operation, place = operation_for_edit(
        draft,
        plan,
        "no meals required, less walking, one place a day, add a cat cafe on day 1",
    )
    assert draft.from_model
    assert (operation, place) == ("add", "cat cafe")
    assert interpret_plan("", _shape_hint(draft)).meals is None
    assert not mark_model_draft(ChangeDraft()).from_model


def test_meal_values_in_the_json_are_kept_without_reading_the_sentence() -> None:
    draft = mark_model_draft(ChangeDraft(new_values={"meals": "drop"}, operation=""))
    shape = interpret_plan("", _shape_hint(draft))
    assert draft.from_model
    assert shape.requested and shape.meals == "drop"
