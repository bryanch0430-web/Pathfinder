"""Streaming must not change what is built, and must fall back on any failure.

The streamed path is only a transport change: the same schema validates it and
the same builder turns it into a plan. These tests pin that down.
"""

from __future__ import annotations

import asyncio

from app.core.llm import LLMError
from app.domain.models import TripContext
from app.plan.builder import compose
from tests.test_builder import _bundle, _draft, _settings


class _StreamOnly:
    def __init__(self, draft):
        self.draft = draft
        self.calls: list[str] = []

    async def json_stream(self, **kwargs):
        self.calls.append("stream")
        if kwargs.get("on_text"):
            await kwargs["on_text"]('{"days": [{"date": "2027-04-10"')
        return self.draft

    async def json(self, **kwargs):
        raise AssertionError("the whole-answer call must not run when the stream works")


class _WholeOnly:
    def __init__(self, draft):
        self.draft = draft
        self.calls: list[str] = []

    async def json(self, **kwargs):
        self.calls.append("whole")
        return self.draft


class _GrowingStream:
    """Calls the listener with more stops on the same day, then a second day."""

    def __init__(self, draft):
        self.draft = draft

    async def json_stream(self, **kwargs):
        on_text = kwargs["on_text"]
        await on_text('{"days": [{"date": "2027-04-10", "title": "Temples", "stops": [{"ref": "at-1"}]')
        await on_text('{"days": [{"date": "2027-04-10", "title": "Temples", "stops": [{"ref": "at-1"}, {"ref": "at-2"}]')
        await on_text(
            '{"days": [{"date": "2027-04-10", "title": "Temples", "stops": [{"ref": "at-1"}, {"ref": "at-2"}]},'
            ' {"date": "2027-04-11", "title": "Second day", "stops": [{"ref": "at-3"}]}]}'
        )
        return self.draft

    async def json(self, **kwargs):
        raise AssertionError("the whole-answer call must not run when the stream works")


def test_a_new_stop_on_a_shown_day_is_emitted_again(monkeypatch):
    ticks = iter(float(value) for value in range(100))
    monkeypatch.setattr("app.plan.builder.time.monotonic", lambda: next(ticks))
    events: list = []
    _compose(_GrowingStream(_draft()), events)
    previews = [data for event, data in events if event == "plan_partial" and data.get("days")]
    assert len(previews) >= 3
    counts = [len(day["stops"]) for preview in previews for day in preview["days"][:1]]
    assert counts[0] == 1
    assert max(counts) == 2


def test_the_preview_starts_before_the_first_day(monkeypatch):
    ticks = iter(float(value) for value in range(100))
    monkeypatch.setattr("app.plan.builder.time.monotonic", lambda: next(ticks))
    events: list = []
    _compose(_GrowingStream(_draft()), events)
    first = events[0]
    assert first[0] == "plan_partial"
    assert first[1].get("started") is True and first[1]["days"] == []


class _StreamFails(_WholeOnly):
    def __init__(self, draft):
        super().__init__(draft)
        self.calls = []

    async def json_stream(self, **kwargs):
        self.calls.append("stream-failed")
        raise LLMError("stream failed (503)", status=503)

    async def json(self, **kwargs):
        self.calls.append("whole")
        return self.draft


def _context() -> TripContext:
    return TripContext(
        session_id="stream-test",
        destination="Kyoto",
        origin="Hong Kong",
        start_date="2027-04-10",
        end_date="2027-04-11",
        days=2,
        party_size=2,
        budget=200000,
        currency="JPY",
        currency_set=True,
    )


_VOLATILE = {"created_at", "updated_at", "generated_at", "fetched_at", "checked_at"}


def _scrub(value):
    """Drop the fields that are a wall-clock stamp, so two builds can be compared."""
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items() if key not in _VOLATILE}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    return value


def _normal(plan):
    return _scrub(plan.model_dump(mode="json"))


def _compose(llm, events: list | None = None):
    async def push(event, data):
        if events is not None:
            events.append((event, data))

    return asyncio.run(
        compose(
            llm=llm,
            settings=_settings(),
            context=_context(),
            bundle=_bundle(),
            disruptions=[],
            notes=[],
            emit=push if events is not None else None,
        )
    )


def test_streaming_builds_the_same_plan_as_the_whole_answer():
    streamed_plan, _ = _compose(_StreamOnly(_draft()), [])
    whole_plan, _ = _compose(_WholeOnly(_draft()))
    assert _normal(streamed_plan) == _normal(whole_plan)


def test_the_partial_event_previews_days_without_changing_the_plan():
    events: list = []
    plan, _ = _compose(_StreamOnly(_draft()), events)
    assert _normal(plan) == _normal(_compose(_WholeOnly(_draft()))[0])
    previews = [data for event, data in events if event == "plan_partial" and data.get("days")]
    assert _normal(plan) == _normal(_compose(_WholeOnly(_draft()))[0])
    assert previews[0]["days"][0]["date"] == "2027-04-10"


def test_a_failed_stream_falls_back_to_the_whole_answer():
    llm = _StreamFails(_draft())
    events: list = []
    plan, _ = _compose(llm, events)
    assert llm.calls == ["stream-failed", "whole"]
    assert _normal(plan) == _normal(_compose(_WholeOnly(_draft()))[0])
    # Only the "a plan is being written" notice: no day was ever previewed.
    notices = [data for event, data in events if event == "plan_partial"]
    assert len(notices) == 1
    assert notices[0].get("started") is True and notices[0]["days"] == []


def test_without_an_emit_callback_the_old_call_is_used():
    llm = _WholeOnly(_draft())
    plan, _ = _compose(llm)
    assert llm.calls == ["whole"]
    assert _normal(plan) == _normal(_compose(_WholeOnly(_draft()))[0])
