"""Audit follow-ups are web searches through the toolbox: logged, capped, bounded, reference-only."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.application import audit_followup
from app.application.audit_followup import run_follow_ups
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.errors import CanaryLeak
from app.domain.models import (
    AgentName,
    AuditFinding,
    AuditFollowUp,
    AuditIssue,
    AuditReport,
    AuditSeverity,
    FailureKind,
    SourceRef,
)
from app.infrastructure.observability import TraceRecorder
from app.infrastructure.providers.websearch import WebResult


class _Store:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def log_tool_call(self, **row: Any) -> None:
        self.calls.append(row)

    def log_injection(self, *args: Any, **kwargs: Any) -> None:
        pass


class _Web:
    available = True

    def __init__(self, rows: list[WebResult] | None = None, delay: float = 0.0, exc: Exception | None = None) -> None:
        self.rows = rows if rows is not None else []
        self.delay = delay
        self.exc = exc
        self.queries: list[str] = []

    async def search(self, *, query: str, count: int = 5, retries: int = 1) -> list[WebResult]:
        self.queries.append(query)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.rows


def _row(title: str, url: str, summary: str = "Hotels and places to stay in Kyoto.") -> WebResult:
    return WebResult(title=title, url=url, summary=summary, snippet=summary, authority=2)


GOOD_ROWS = [
    _row("Kyoto hotels guide", "https://guide.example/kyoto-hotels"),
    _row("Where to stay in Kyoto", "https://stay.example/kyoto"),
]


def _finding(agent: AgentName = AgentName.HOTEL, query: str | None = "Kyoto hotel opening status") -> AuditFinding:
    return AuditFinding(
        agent=agent,
        item_id="h1",
        issue=AuditIssue.SUSPECT,
        severity=AuditSeverity.WARN,
        detail="The hotel may be closed.",
        follow_up=AuditFollowUp(query=query) if query else None,
    )


def _report(*findings: AuditFinding, skipped: str | None = None) -> AuditReport:
    return AuditReport(findings=list(findings), skipped=skipped)


def _toolbox(
    settings: Settings, max_calls: int | None = None
) -> tuple[Toolbox, TraceRecorder, _Store]:
    store = _Store()
    tracer = TraceRecorder("req_followup", settings)
    toolbox = Toolbox(path="plan", tracer=tracer, store=store, request_id="req_followup", max_calls=max_calls)
    return toolbox, tracer, store


def _run(report: AuditReport, registry: Any, toolbox: Toolbox, settings: Settings) -> AuditReport:
    return asyncio.run(run_follow_ups(report, registry=registry, toolbox=toolbox, city="Kyoto", settings=settings))


def test_follow_up_attaches_references(settings: Settings) -> None:
    web = _Web(GOOD_ROWS)
    toolbox, _, _ = _toolbox(settings)
    report = _report(_finding(query=None), _finding())

    out = _run(report, SimpleNamespace(web=[web], web_deep=[]), toolbox, settings)

    assert len(out.evidence) == 1
    evidence = out.evidence[0]
    assert evidence.finding_index == 1
    assert evidence.query == "Kyoto hotel opening status"
    assert evidence.gap is None
    assert len(evidence.references) == 2
    assert all(isinstance(ref, SourceRef) for ref in evidence.references)
    assert {ref.url for ref in evidence.references} == {row.url for row in GOOD_ROWS}
    assert report.evidence == []  # the input report is not changed


def test_follow_up_goes_through_toolbox(settings: Settings) -> None:
    web = _Web(GOOD_ROWS)
    toolbox, tracer, store = _toolbox(settings)
    report = _report(_finding(), _finding(query="Kyoto ryokan reviews"))

    _run(report, SimpleNamespace(web=[web], web_deep=[]), toolbox, settings)

    assert sorted(web.queries) == ["Kyoto hotel opening status", "Kyoto ryokan reviews"]
    assert [row["tool"] for row in store.calls] == ["web.search", "web.search"]
    assert len([span for span in tracer.spans if span.name == "tool.web.search"]) == 2
    assert toolbox.calls == 2


def test_follow_up_without_web_source(settings: Settings) -> None:
    toolbox, _, store = _toolbox(settings)

    out = _run(_report(_finding()), SimpleNamespace(web=[], web_deep=[]), toolbox, settings)

    assert out.evidence[0].references == []
    assert out.evidence[0].gap is not None
    assert out.evidence[0].gap.kind == FailureKind.NO_DATA
    assert store.calls == []


def test_follow_up_tool_budget_exhausted(settings: Settings) -> None:
    web = _Web(GOOD_ROWS)
    toolbox, _, _ = _toolbox(settings, max_calls=0)

    out = _run(_report(_finding()), SimpleNamespace(web=[web], web_deep=[]), toolbox, settings)

    assert out.evidence[0].references == []
    assert out.evidence[0].gap is not None
    assert out.evidence[0].gap.kind == FailureKind.BLOCKED
    assert web.queries == []


def test_follow_up_timeout(settings: Settings) -> None:
    web = _Web(GOOD_ROWS, delay=5.0)
    quick = settings.model_copy(update={"audit_follow_up_seconds": 0.05})
    toolbox, _, _ = _toolbox(quick)
    report = _report(_finding(), _finding(query="Kyoto ryokan reviews"))

    out = _run(report, SimpleNamespace(web=[web], web_deep=[]), toolbox, quick)

    assert [e.finding_index for e in out.evidence] == [0, 1]
    assert all(e.gap is not None and e.gap.kind == FailureKind.TIMEOUT for e in out.evidence)
    assert all(e.references == [] for e in out.evidence)


def test_a_follow_up_that_finished_keeps_its_references_when_another_times_out(settings: Settings) -> None:
    class _Mixed(_Web):
        async def search(self, *, query: str, count: int = 5, retries: int = 1) -> list[WebResult]:
            if "slow" in query:
                await asyncio.sleep(5.0)
            return self.rows

    quick = settings.model_copy(update={"audit_follow_up_seconds": 1.0})
    toolbox, _, _ = _toolbox(quick)
    report = _report(_finding(query="Kyoto fast query"), _finding(query="Kyoto slow query"))

    out = _run(report, SimpleNamespace(web=[_Mixed(GOOD_ROWS)], web_deep=[]), toolbox, quick)

    assert len(out.evidence[0].references) == 2 and out.evidence[0].gap is None
    assert out.evidence[1].references == [] and out.evidence[1].gap.kind == FailureKind.TIMEOUT


def test_a_skipped_report_or_one_without_follow_ups_is_unchanged_and_makes_no_calls(settings: Settings) -> None:
    web = _Web(GOOD_ROWS)
    toolbox, _, store = _toolbox(settings)
    registry = SimpleNamespace(web=[web], web_deep=[])

    skipped = _report(_finding(), skipped="audit timed out")
    plain = _report(_finding(query=None))

    assert _run(skipped, registry, toolbox, settings) == skipped
    assert _run(plain, registry, toolbox, settings) == plain
    assert web.queries == [] and store.calls == []


def test_references_without_a_url_are_dropped_and_only_reference_fields_exist(settings: Settings) -> None:
    rows = [_row("No link", ""), _row("Kyoto hotels guide", "https://guide.example/kyoto-hotels")]
    toolbox, _, _ = _toolbox(settings)

    out = _run(_report(_finding()), SimpleNamespace(web=[_Web(rows)], web_deep=[]), toolbox, settings)

    refs = out.evidence[0].references
    assert [ref.url for ref in refs] == ["https://guide.example/kyoto-hotels"]
    assert set(SourceRef.model_fields) == {"title", "url", "source", "fetched_at", "published_at"}


def test_a_ticket_finding_searches_the_flight_section(settings: Settings) -> None:
    """The lookup knows the section "flight"; an unknown name would make every page irrelevant."""
    rows = [_row("Kyoto flights", "https://fly.example/kyoto", "Cheap flight options to Kyoto.")]
    toolbox, _, _ = _toolbox(settings)
    report = _report(_finding(agent=AgentName.TICKET, query="Kyoto flight schedule"))

    out = _run(report, SimpleNamespace(web=[_Web(rows)], web_deep=[]), toolbox, settings)

    assert len(out.evidence[0].references) == 1


def test_a_source_that_raises_becomes_a_gap_not_an_exception(settings: Settings) -> None:
    toolbox, _, _ = _toolbox(settings)
    registry = SimpleNamespace(web=[_Web(exc=RuntimeError("boom"))], web_deep=[])

    out = _run(_report(_finding()), registry, toolbox, settings)

    assert out.evidence[0].references == [] and out.evidence[0].gap is not None


def test_a_cancelled_outer_task_still_propagates(settings: Settings) -> None:
    registry = SimpleNamespace(web=[_Web(GOOD_ROWS, delay=5.0)], web_deep=[])
    toolbox, _, _ = _toolbox(settings)

    async def scenario() -> None:
        task = asyncio.ensure_future(
            run_follow_ups(_report(_finding()), registry=registry, toolbox=toolbox, city="Kyoto", settings=settings)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_a_canary_leak_in_a_follow_up_search_is_raised(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """The one exception to failing open: a leaked canary leaves run_follow_ups."""
    async def leak(**_: Any) -> Any:
        raise CanaryLeak("leak")

    monkeypatch.setattr(audit_followup, "lookup_web_references", leak)
    toolbox, _, _ = _toolbox(settings)
    with pytest.raises(CanaryLeak):
        _run(_report(_finding()), SimpleNamespace(web=[_Web(GOOD_ROWS)], web_deep=[]), toolbox, settings)
