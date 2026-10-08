"""LangfuseSink against a fake client (no network, no real SDK client)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from backend.observability.langfuse_sink import LangfuseSink
from backend.observability.recorder import Observability
from backend.schemas.common import AgentName, PathName, ToolName
from backend.schemas.llm import ChatMessage, LLMRequest, Role, TokenUsage
from backend.schemas.observability import LLMCallRecord, ToolCallRecord, TraceEvent
from backend.schemas.tools import (
    FailureKind,
    FieldIssue,
    SearchHit,
    SearchPayload,
    ToolOperation,
    ToolOutcomeStatus,
)
from backend.settings import Settings

T0 = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)


@dataclass
class FakeObservation:
    kind: str  # "span" | "generation" | "event"
    name: str
    trace_id: str
    parent: FakeObservation | None
    kwargs: dict[str, object]
    updates: list[dict[str, object]] = field(default_factory=list)
    trace_updates: list[dict[str, object]] = field(default_factory=list)
    ended: bool = False
    end_time: int | None = None

    def update(self, **kwargs: object) -> FakeObservation:
        self.updates.append(kwargs)
        return self

    def update_trace(self, **kwargs: object) -> FakeObservation:
        self.trace_updates.append(kwargs)
        return self

    def end(self, *, end_time: int | None = None) -> FakeObservation:
        self.ended = True
        self.end_time = end_time
        return self

    def start_span(self, name: str, **kwargs: object) -> FakeObservation:
        return self.client_ref.make("span", name, self.trace_id, self, kwargs)

    def start_observation(self, *, name: str, as_type: str, **kwargs: object) -> FakeObservation:
        return self.client_ref.make(as_type, name, self.trace_id, self, kwargs)

    def create_event(self, *, name: str, **kwargs: object) -> FakeObservation:
        return self.client_ref.make("event", name, self.trace_id, self, kwargs)

    client_ref: FakeClient = field(default=None, repr=False)  # type: ignore[assignment]


class FakeClient:
    def __init__(self) -> None:
        self.observations: list[FakeObservation] = []
        self.flushed = 0

    def make(
        self, kind: str, name: str, trace_id: str, parent: FakeObservation | None, kwargs: dict[str, object]
    ) -> FakeObservation:
        obs = FakeObservation(kind, name, trace_id, parent, dict(kwargs), client_ref=self)
        self.observations.append(obs)
        return obs

    def start_span(self, *, name: str, trace_context: dict[str, str] | None = None, **kwargs: object) -> FakeObservation:
        assert trace_context is not None
        return self.make("span", name, trace_context["trace_id"], None, kwargs)

    def start_observation(
        self, *, name: str, as_type: str, trace_context: dict[str, str] | None = None, **kwargs: object
    ) -> FakeObservation:
        assert trace_context is not None
        return self.make(as_type, name, trace_context["trace_id"], None, kwargs)

    def create_event(self, *, name: str, trace_context: dict[str, str] | None = None, **kwargs: object) -> FakeObservation:
        assert trace_context is not None
        return self.make("event", name, trace_context["trace_id"], None, kwargs)

    def flush(self) -> None:
        self.flushed += 1

    def of_kind(self, kind: str) -> list[FakeObservation]:
        return [o for o in self.observations if o.kind == kind]


def llm_record(trace_id: str, *, error: str | None = None) -> LLMCallRecord:
    return LLMCallRecord(
        record_id="l1",
        trace_id=trace_id,
        purpose="planner",
        path=PathName.PLAN,
        agent=AgentName.HOTEL,
        model="grok-test",
        request=LLMRequest(
            purpose="planner",
            model="grok-test",
            messages=[ChatMessage(role=Role.SYSTEM, content="be brief"), ChatMessage(role=Role.USER, content="plan Kyoto")],
            temperature=0.2,
            max_tokens=512,
        ),
        response_content=None if error else '{"days": []}',
        usage=None if error else TokenUsage(input_tokens=42, output_tokens=7),
        error=error,
        started_at=T0,
        ended_at=T0 + timedelta(milliseconds=250),
        latency_ms=250.0,
    )


def tool_record(trace_id: str, *, ok: bool = True) -> ToolCallRecord:
    return ToolCallRecord(
        record_id="t1",
        trace_id=trace_id,
        call_id="call-1",
        path=PathName.PLAN,
        agent=AgentName.ATTRACTION,
        operation=ToolOperation.WEB_SEARCH,
        tool=ToolName.WEB_SEARCH,
        raw_request='{"operation": "web_search", "query": "kyoto temples", "destination": "Kyoto"}',
        status=ToolOutcomeStatus.OK if ok else ToolOutcomeStatus.VALIDATION_ERROR,
        failure=None if ok else FailureKind.VALIDATION,
        issues=[] if ok else [FieldIssue(loc="query", message="bad", kind="value_error")],
        attempts=2,
        provider="mock",
        payload=SearchPayload(hits=[SearchHit(title="Kinkaku-ji", url="https://example.test/k", snippet="gold")]) if ok else None,
        fetched_at=T0 if ok else None,
        started_at=T0,
        ended_at=T0 + timedelta(milliseconds=40),
        latency_ms=40.0,
    )


@pytest.fixture
def client() -> FakeClient:
    return FakeClient()


@pytest.fixture
def obs(client: FakeClient) -> Observability:
    return Observability(Settings(), sinks=[LangfuseSink(Settings(), client=client)])


def test_trace_start_creates_root_span_bound_to_our_trace_id(obs: Observability, client: FakeClient) -> None:
    trace = obs.start_trace(name="chat_turn", session_id="sess-1")
    [root] = client.of_kind("span")
    assert root.trace_id == trace.trace_id
    assert root.name == "chat_turn"
    assert root.parent is None
    assert root.trace_updates == [{"name": "chat_turn", "session_id": "sess-1"}]
    assert root.ended is False


def test_llm_call_becomes_a_generation_under_the_root(obs: Observability, client: FakeClient) -> None:
    trace = obs.start_trace(name="chat_turn", session_id="sess-1")
    obs.record_llm_call(llm_record(trace.trace_id))
    [generation] = client.of_kind("generation")
    [root] = client.of_kind("span")
    assert generation.parent is root
    assert generation.trace_id == trace.trace_id
    assert generation.name == "planner"
    kwargs = generation.kwargs
    assert kwargs["model"] == "grok-test"
    assert kwargs["input"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "plan Kyoto"},
    ]
    assert kwargs["output"] == '{"days": []}'
    assert kwargs["usage_details"] == {"input": 42, "output": 7}
    assert kwargs["metadata"]["path"] == "plan"  # type: ignore[index]
    assert kwargs["metadata"]["agent"] == "hotel"  # type: ignore[index]
    assert "level" not in kwargs
    assert generation.ended and generation.end_time is not None


def test_failed_llm_call_is_marked_as_error(obs: Observability, client: FakeClient) -> None:
    trace = obs.start_trace(name="t", session_id="s")
    obs.record_llm_call(llm_record(trace.trace_id, error="upstream 503"))
    [generation] = client.of_kind("generation")
    assert generation.kwargs["level"] == "ERROR"
    assert generation.kwargs["status_message"] == "upstream 503"
    assert generation.kwargs["metadata"]["error"] == "upstream 503"  # type: ignore[index]
    assert "usage_details" not in generation.kwargs


def test_tool_call_becomes_a_span_named_after_the_operation(obs: Observability, client: FakeClient) -> None:
    trace = obs.start_trace(name="chat_turn", session_id="s")
    obs.record_tool_call(tool_record(trace.trace_id))
    obs.record_tool_call(tool_record(trace.trace_id, ok=False))
    root, ok_span, bad_span = client.of_kind("span")
    assert ok_span.parent is root and ok_span.trace_id == trace.trace_id
    assert ok_span.name == "tool:web_search"
    assert ok_span.kwargs["input"].startswith('{"operation": "web_search"')  # type: ignore[union-attr]
    assert ok_span.kwargs["output"]["hits"][0]["title"] == "Kinkaku-ji"  # type: ignore[index]
    meta = ok_span.kwargs["metadata"]
    assert meta["status"] == "ok" and meta["attempts"] == 2 and meta["provider"] == "mock"  # type: ignore[index]
    assert meta["call_id"] == "call-1" and meta["fetched_at"] == T0.isoformat()  # type: ignore[index]
    assert "failure" not in meta  # type: ignore[operator]
    assert "level" not in ok_span.kwargs
    assert ok_span.ended

    assert bad_span.kwargs["output"] == {"issues": [{"loc": "query", "message": "bad", "kind": "value_error"}]}
    assert bad_span.kwargs["metadata"]["failure"] == "validation"  # type: ignore[index]
    assert bad_span.kwargs["level"] == "WARNING"


def test_event_and_trace_end(obs: Observability, client: FakeClient) -> None:
    trace = obs.start_trace(name="chat_turn", session_id="s")
    obs.record_event(trace, "gate", {"route": "plan", "confidence": 0.9})
    [event] = client.of_kind("event")
    [root] = client.of_kind("span")
    assert event.parent is root and event.name == "gate"
    assert event.kwargs["metadata"]["route"] == "plan"  # type: ignore[index]

    obs.end_trace(trace, output="final reply")
    assert root.ended
    assert {"output": "final reply"} in root.updates
    assert {"output": "final reply"} in root.trace_updates
    obs.flush()
    assert client.flushed == 1


def test_records_for_an_unknown_or_ended_trace_still_reach_langfuse(obs: Observability, client: FakeClient) -> None:
    trace = obs.start_trace(name="t", session_id="s")
    obs.end_trace(trace)
    obs.record_llm_call(llm_record(trace.trace_id))  # late record: root is gone
    stray = "ab" * 16
    obs.record_tool_call(tool_record(stray))
    obs.record_event(trace.model_copy(update={"trace_id": stray}), "orphan")
    [generation] = client.of_kind("generation")
    assert generation.trace_id == trace.trace_id and generation.parent is None
    stray_span = [o for o in client.of_kind("span") if o.name == "tool:web_search"]
    assert stray_span[0].trace_id == stray
    assert client.of_kind("event")[0].trace_id == stray


def test_double_end_is_harmless(obs: Observability, client: FakeClient) -> None:
    trace = obs.start_trace(name="t", session_id="s")
    obs.end_trace(trace, output="a")
    obs.end_trace(trace, output="b")
    [root] = client.of_kind("span")
    assert root.updates == [{"output": "a"}]


def test_sink_methods_work_directly_with_event_model(client: FakeClient) -> None:
    sink = LangfuseSink(Settings(), client=client)
    sink.on_event(TraceEvent(trace_id="cd" * 16, name="direct", attributes={"k": 1}))
    assert client.of_kind("event")[0].name == "direct"


def test_client_is_only_built_when_not_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr

    created: list[dict[str, str]] = []

    class StubLangfuse:
        def __init__(self, **kwargs: str) -> None:
            created.append(kwargs)

    import langfuse

    monkeypatch.setattr(langfuse, "Langfuse", StubLangfuse)
    settings = Settings(
        langfuse_public_key=SecretStr("pk-test"), langfuse_secret_key=SecretStr("sk-test"), langfuse_host="http://lf.test"
    )
    LangfuseSink(settings, client=FakeClient())
    assert created == []
    LangfuseSink(settings)
    assert created == [{"public_key": "pk-test", "secret_key": "sk-test", "host": "http://lf.test"}]
