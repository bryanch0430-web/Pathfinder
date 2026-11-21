"""An edit is checked, a short theme-park visit is lengthened once, then the writer phrases it."""

import asyncio
from types import SimpleNamespace

from app.application.modification import _separate_overlaps
from app.application.turn_graph import review_and_reply
from app.application.validation import validate_plan
from app.interface.local_admin import local_admin_for
from tests.plan_samples import make_plan, meal, stop


class _Gateway:
    available = True

    def __init__(self) -> None:
        self.llm = SimpleNamespace(name="writer-test")
        self.reviews = 0

    async def json(self, prompt, *, user, purpose):
        self.reviews += 1
        assert prompt == "turn-review"
        assert purpose == "turn.review"
        assert "Ocean Park" in user
        return {"verdict": "pass", "fix": "none", "issue": ""}

    async def text(self, prompt, *, user, purpose, temperature, max_tokens, reasoning):
        assert "180" in user or "09:00" in user or "now runs" in user
        return "Ocean Park has a full visit on that day, and the other stops stay around it."


def test_a_one_hour_theme_park_is_lengthened_before_the_reply() -> None:
    plan = make_plan(days=1, with_tickets=False, with_stay=False, stops_per_day=0)
    plan.days[0].items.append(stop("park", "Ocean Park", "10:00", "11:00"))
    gateway = _Gateway()

    steps: list[dict] = []
    reply = asyncio.run(
        review_and_reply(
            SimpleNamespace(reviewer_model="deepseek-v4-pro"),
            gateway,
            message="add ocean park for 1 hour on day 1",
            history=(),
            plan=plan,
            facts="Added Ocean Park on Day 1 at 10:00.",
            route_model="jev-latest",
            on_step=steps.append,
        )
    )

    park = next(item for item in plan.days[0].items if item.name == "Ocean Park")
    # Lunch already occupies 13:00–14:00, so the five hours move to the next free gap.
    # The lunch stays. A review does not delete it to keep the original start.
    assert (park.start, park.end) == ("14:00", "19:00")
    lunch = next(item for item in plan.days[0].items if item.name.startswith("Soba"))
    assert (lunch.start, lunch.end) == ("13:00", "14:00")
    assert reply.startswith("Ocean Park has a full visit")
    assert "Models:" not in reply
    phases = [(step["phase"], step["status"]) for step in steps]
    assert ("review", "running") in phases
    assert ("review", "done") in phases
    assert ("reply", "done") in phases
    assert gateway.reviews == 2


def test_a_pass_with_nothing_short_asks_the_reviewer_once() -> None:
    plan = make_plan(days=1, with_tickets=False, with_stay=False, stops_per_day=0)
    plan.days[0].items.append(stop("park", "Ocean Park", "10:00", "15:00"))
    gateway = _Gateway()

    reply = asyncio.run(
        review_and_reply(
            SimpleNamespace(reviewer_model=""),
            gateway,
            message="add ocean park on day 1",
            history=(),
            plan=plan,
            facts="Added Ocean Park on Day 1 at 10:00.",
            route_model="",
        )
    )

    park = next(item for item in plan.days[0].items if item.name == "Ocean Park")
    assert (park.start, park.end) == ("10:00", "15:00")
    assert gateway.reviews == 1
    assert reply.startswith("Added Ocean Park")
    assert "Models:" not in reply


def test_an_overlap_is_separated_instead_of_left_illegal() -> None:
    plan = make_plan(days=1, with_tickets=False, with_stay=False, stops_per_day=0)
    plan.days[0].items = [
        meal("mott", "Mott 32", "12:30", "13:30"),
        meal("pici", "Pici (Central)", "13:15", "14:15"),
    ]

    notes = _separate_overlaps(plan)

    assert plan.days[0].items[1].start == "13:30"
    assert plan.days[0].items[1].end == "14:30"
    assert "overlapped" in notes[0]
    assert not any("overlap" in error for error in validate_plan(plan, SimpleNamespace(max_days=30)))


def test_a_duration_search_is_shown_to_the_reviewer() -> None:
    plan = make_plan(days=1, with_tickets=False, with_stay=False, stops_per_day=0)
    plan.days[0].items.append(stop("park", "Ocean Park", "10:00", "14:00"))
    seen: list[str] = []

    class Gateway(_Gateway):
        async def json(self, prompt, *, user, purpose):
            seen.append(user)
            return await super().json(prompt, user=user, purpose=purpose)

    class Page:
        title = "How long to spend at Ocean Park"
        snippet = "Most visits take a full day, about 8 hours."

    async def search(query: str):
        assert "Ocean Park" in query
        return [Page()]

    asyncio.run(
        review_and_reply(
            SimpleNamespace(reviewer_model=""),
            Gateway(),
            message="add ocean park on day 1",
            history=(),
            plan=plan,
            facts="Added Ocean Park.",
            search=search,
        )
    )

    assert seen and "Duration pages" in seen[0]
    assert "8 hours" in seen[0]
    assert "Reasonableness skill" in seen[0]
    assert "5 hours" in seen[0]


def test_a_blocked_theme_park_asks_before_a_stop_is_removed() -> None:
    plan = make_plan(days=1, with_tickets=False, with_stay=False, stops_per_day=0)
    plan.days[0].items = [
        stop("park", "Ocean Park", "09:00", "11:00"),
        stop("disney", "Hong Kong Disneyland", "11:00", "21:00"),
    ]

    permissions: list[dict] = []
    reply = asyncio.run(
        review_and_reply(
            SimpleNamespace(reviewer_model=""),
            _Gateway(),
            message="add ocean park on day 1",
            history=(),
            plan=plan,
            facts="Added Ocean Park on Day 1 at 09:00.",
            phrase=False,
            permissions=permissions,
        )
    )

    names = [item.name for item in plan.days[0].items]
    assert names == ["Ocean Park", "Hong Kong Disneyland"]
    assert plan.days[0].items[0].end == "11:00"
    assert len(permissions) == 1
    assert "Remove Hong Kong Disneyland" in permissions[0]["label"]
    assert "Nothing is added or removed until you accept" in reply
    kept = [item["name"] for day in permissions[0]["plan"]["days"] for item in day["items"]]
    assert kept == ["Ocean Park"]
    assert permissions[0]["plan"]["days"][0]["items"][0]["end"] == "14:00"


def test_the_reviewer_reasoning_is_written_into_the_decision_log(monkeypatch) -> None:
    class Result:
        text = '{"verdict":"retry","fix":"none","issue":"Lunch is still at 12:30."}'
        model = "deepseek-v4-pro"
        prompt_tokens = 11
        completion_tokens = 7
        latency_ms = 17800
        reasoning = "The request asked for 15:00 and the plan still shows 12:30."

    class Reviewer:
        available = True
        name = "deepseek-v4-pro"

        async def complete(self, **_kwargs):
            return Result()

    monkeypatch.setattr("app.application.plan_review.reviewer_llm", lambda _settings: Reviewer())
    recorded: list[dict] = []

    class Tracer:
        def record_generation(self, **kwargs):
            recorded.append(kwargs)

    class Gateway(_Gateway):
        tracer = Tracer()

    plan = make_plan(days=1, with_tickets=False, with_stay=False, stops_per_day=0)
    plan.days[0].items.append(stop("park", "Ocean Park", "10:00", "15:00"))
    asyncio.run(
        review_and_reply(
            SimpleNamespace(reviewer_model="deepseek-v4-pro"),
            Gateway(),
            message="why lunch at 1230, can it be 3pm?",
            history=(),
            plan=plan,
            facts="The days would start at 12:30.",
        )
    )

    assert len(recorded) == 1
    call = recorded[0]
    assert call["purpose"] == "turn.review"
    assert call["model"] == "deepseek-v4-pro"
    assert "15:00" in call["reasoning"]
    assert "Lunch is still at 12:30" in call["output_preview"]
    assert "why lunch at 1230" in call["user_preview"]


def test_a_failed_review_is_still_written_into_the_decision_log(monkeypatch) -> None:
    class Reviewer:
        available = True
        name = "deepseek-v4-pro"

        async def complete(self, **_kwargs):
            raise TimeoutError("review budget")

    monkeypatch.setattr("app.application.plan_review.reviewer_llm", lambda _settings: Reviewer())
    recorded: list[dict] = []

    class Tracer:
        def record_generation(self, **kwargs):
            recorded.append(kwargs)

    class Gateway(_Gateway):
        tracer = Tracer()

    plan = make_plan(days=1, with_tickets=False, with_stay=False, stops_per_day=0)
    plan.days[0].items.append(stop("park", "Ocean Park", "10:00", "15:00"))
    steps: list[dict] = []
    asyncio.run(
        review_and_reply(
            SimpleNamespace(reviewer_model="deepseek-v4-pro", reviewer_timeout_seconds=20),
            Gateway(),
            message="add ocean park on day 1",
            history=(),
            plan=plan,
            facts="Added Ocean Park on Day 1 at 10:00.",
            on_step=steps.append,
            phrase=False,
        )
    )

    assert len(recorded) == 1
    call = recorded[0]
    assert call["purpose"] == "turn.review"
    assert call["model"] == "deepseek-v4-pro"
    assert "No answer (TimeoutError)" in call["output_preview"]
    assert "Reasonableness skill" in call["user_preview"] or "Traveller:" in call["user_preview"]
    done = [step for step in steps if step.get("phase") == "review" and step.get("status") == "done"]
    assert done and done[0]["model"] == "deepseek-v4-pro"


def test_a_turn_step_is_kept_once_and_updated_when_it_finishes() -> None:
    from app.config import Settings
    from app.infrastructure.observability import TracerProxy, note_turn_step, start_trace

    tracer = start_trace(Settings(_env_file=None), "req-steps")
    TracerProxy().bind(tracer)
    note_turn_step({"id": "review-1", "phase": "review", "model": "deepseek-v4-pro", "status": "running"})
    note_turn_step({"id": "review-1", "phase": "review", "model": "deepseek-v4-pro", "status": "done", "seconds": 17.8})
    note_turn_step({"id": "clock-1", "phase": "clock", "model": "application", "status": "done", "seconds": 0})

    steps = tracer.metadata["turn_steps"]
    assert [step["id"] for step in steps] == ["review-1", "clock-1"]
    assert steps[0]["status"] == "done"
    assert steps[0]["seconds"] == 17.8


def test_the_named_proxy_opens_local_admin() -> None:
    class _Settings:
        local_admin_active = True
        local_admin_proxy = "172.24.135.220"

    class _Client:
        host = "172.24.135.220"

    class _Request:
        client = _Client()
        headers = {"host": "172.24.128.1:8100"}
        method = "GET"

    assert local_admin_for(_Request(), _Settings())

    class _Other:
        local_admin_active = True
        local_admin_proxy = "172.24.135.220"

    class _Stranger:
        host = "192.168.1.20"

    class _Lan:
        client = _Stranger()
        headers = {"host": "192.168.1.8:8100"}
        method = "GET"

    assert not local_admin_for(_Lan(), _Other())
