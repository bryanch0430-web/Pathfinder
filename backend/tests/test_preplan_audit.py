"""Audit report models and the off-by-default pre-planning audit settings."""
from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest
from pydantic import ValidationError

from app.application.auditor import audit_results
from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.config import Settings
from app.domain.errors import CanaryLeak
from app.domain.models import (
    AgentName,
    AgentResult,
    AgentStatus,
    AuditFinding,
    AuditFollowUp,
    AuditIssue,
    AuditReport,
    AuditSeverity,
    DateRange,
    TripContext,
)
from app.eval_metering import MeteredLLM
from app.infrastructure.llm import LLMResult, NullLLM
from app.infrastructure.observability import NoopTracer


def test_audit_settings_default_off():
    assert Settings().enable_preplan_audit is False
    assert Settings().audit_max_follow_ups == 3


def test_follow_up_query_bounds():
    with pytest.raises(ValidationError):
        AuditFollowUp(query="x" * 161)
    with pytest.raises(ValidationError):
        AuditFollowUp(query="ab")


def test_blocked_ids():
    report = AuditReport(
        findings=[
            AuditFinding(
                agent=AgentName.ATTRACTION, item_id="p1", issue=AuditIssue.SUSPECT,
                severity=AuditSeverity.BLOCK, detail="closed for good",
            ),
            AuditFinding(
                agent=AgentName.ATTRACTION, item_id="p2", issue=AuditIssue.STALE,
                severity=AuditSeverity.WARN, detail="hours may be old",
            ),
            AuditFinding(
                agent=AgentName.HOTEL, item_id=None, issue=AuditIssue.MISSING,
                severity=AuditSeverity.BLOCK, detail="no hotel results",
            ),
        ]
    )
    assert report.blocked_ids == {"p1"}


# ---- Task 2: the auditor -------------------------------------------------


def _gateway(settings: Settings, llm) -> LLMGateway:
    return LLMGateway(llm, PromptLibrary(settings), NoopTracer(), settings)


def _results(places: int = 1) -> dict[str, AgentResult]:
    rows = [
        {
            "id": f"p{i + 1}", "name": f"Place {i + 1}", "category": "attraction", "rating": 4.5,
            "review_count": 100, "address": "1 Main St", "opening_hours": ["Mon 9-5"],
            "source_refs": [{"title": "Site", "url": "https://a.example", "source": "osm"}],
        }
        for i in range(places)
    ]
    return {
        "attraction": AgentResult(
            agent=AgentName.ATTRACTION, status=AgentStatus.OK, payload={"places": rows, "meals": []}
        ),
        "hotel": AgentResult(
            agent=AgentName.HOTEL, status=AgentStatus.OK,
            payload={"hotels": [{
                "id": "h1", "name": "Hotel One", "area": "Centre", "rating": 4.0,
                "nightly_price": {"amount": 120, "currency": "USD"},
            }]},
        ),
        "weather": AgentResult(
            agent=AgentName.WEATHER, status=AgentStatus.OK,
            payload={"days": [{"date": "2026-12-01", "summary": "Clear", "high_c": 20}]},
        ),
        "ticket": AgentResult(agent=AgentName.TICKET, status=AgentStatus.UNAVAILABLE, payload={}),
    }


def _context() -> TripContext:
    return TripContext(session_id="s1", destination="Tokyo", party_size=2)


def _range() -> DateRange:
    return DateRange(start=date(2026, 12, 1), end=date(2026, 12, 3))


def _finding(item_id, severity="warn", follow_up=None, agent="attraction") -> dict:
    return {
        "agent": agent, "item_id": item_id, "issue": "suspect_fact", "severity": severity,
        "detail": "doubtful", "follow_up": follow_up,
    }


def _reply(*findings) -> str:
    return json.dumps({"findings": list(findings)})


def _run(settings, reply, results=None):
    llm = MeteredLLM(replies={"preplan.audit": reply})
    report = asyncio.run(audit_results(
        results=results or _results(), context=_context(), date_range=_range(),
        gateway=_gateway(settings, llm), settings=settings,
    ))
    return report, llm


def test_audit_keeps_known_item_finding(settings):
    report, llm = _run(settings, _reply(_finding("p1")))
    assert len(report.findings) == 1
    assert report.skipped is None
    assert report.evidence == []
    assert llm.purposes == ["preplan.audit"]


def test_audit_drops_finding_for_unknown_item_id(settings):
    report, _ = _run(settings, _reply(_finding("zzz")))
    assert report.findings == []


def test_audit_keeps_section_level_finding_and_checks_ids_per_agent(settings):
    report, _ = _run(settings, _reply(
        _finding(None, agent="hotel"),
        _finding("h1", agent="hotel"),
        _finding("h1", agent="attraction"),
        _finding("2026-12-01", agent="weather"),
    ))
    assert [(f.agent.value, f.item_id) for f in report.findings] == [
        ("hotel", None), ("hotel", "h1"), ("weather", "2026-12-01"),
    ]


def test_audit_caps_follow_ups_by_severity(settings):
    settings = settings.model_copy(update={"audit_max_follow_ups": 3})
    fu = {"query": "is it open"}
    reply = _reply(
        _finding("p1", "info", fu), _finding("p2", "info", fu), _finding("p3", "info", fu),
        _finding("p4", "block", fu), _finding("p5", "block", fu),
    )
    report, _ = _run(settings, reply, _results(places=5))
    assert len(report.findings) == 5
    kept = [f for f in report.findings if f.follow_up is not None]
    assert len(kept) == 3
    assert {f.item_id for f in kept if f.severity == AuditSeverity.BLOCK} == {"p4", "p5"}


def test_audit_drops_follow_up_query_with_url(settings):
    for query in ("see https://evil.example now", "visit WWW.evil.example"):
        report, _ = _run(settings, _reply(_finding("p1", "block", {"query": query})))
        assert len(report.findings) == 1
        assert report.findings[0].follow_up is None


def test_audit_fail_open_on_parse_error(settings):
    report, _ = _run(settings, "not json")
    assert report.findings == []
    assert report.skipped.startswith("audit failed")


def test_audit_skipped_without_model(settings):
    gateway = _gateway(settings, NullLLM())
    report = asyncio.run(audit_results(
        results=_results(), context=_context(), date_range=_range(), gateway=gateway, settings=settings,
    ))
    assert report.skipped == "no model"
    assert report.findings == []
    assert gateway.calls == 0


def test_audit_timeout(settings):
    settings = settings.model_copy(update={"audit_timeout_seconds": 0.05})

    class Slow:
        name = "slow"
        available = True

        async def complete(self, **_):
            await asyncio.sleep(1)
            return LLMResult(text='{"findings": []}', model="slow")

    report = asyncio.run(audit_results(
        results=_results(), context=_context(), date_range=_range(),
        gateway=_gateway(settings, Slow()), settings=settings,
    ))
    assert report.skipped.startswith("audit failed")


def test_audit_canary_leak_propagates(settings, monkeypatch):
    gateway = _gateway(settings, MeteredLLM())

    async def leak(*_, **__):
        raise CanaryLeak("leak")

    monkeypatch.setattr(gateway, "json", leak)
    with pytest.raises(CanaryLeak):
        asyncio.run(audit_results(
            results=_results(), context=_context(), date_range=_range(), gateway=gateway, settings=settings,
        ))


def test_audit_payload_is_bounded(settings):
    settings = settings.model_copy(update={"audit_max_items_per_agent": 15})
    seen: list[str] = []

    class Recording:
        name = "rec"
        available = True

        async def complete(self, *, user, **_):
            seen.append(user)
            return LLMResult(text='{"findings": []}', model="rec")

    report = asyncio.run(audit_results(
        results=_results(places=40), context=_context(), date_range=_range(),
        gateway=_gateway(settings, Recording()), settings=settings,
    ))
    assert report.skipped is None
    sent = json.loads(seen[0])
    assert len(sent["agents"]["attraction"]["items"]) == 15
    assert sent["trip"]["destination"] == "Tokyo"
    assert sent["agents"]["ticket"]["status"] == "unavailable"
