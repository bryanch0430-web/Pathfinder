"""The planner uses the pre-planning audit: capped blocks drop candidates, findings reach the payload."""

import asyncio
import json
from datetime import date, timedelta

from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner, apply_audit_blocks
from app.application.prompts import PromptLibrary
from app.config import Settings
from app.domain.models import (
    AgentName,
    AuditFinding,
    AuditIssue,
    AuditReport,
    AuditSeverity,
    DateRange,
    FollowUpEvidence,
    GeoLocation,
    GeoPoint,
    SourceRef,
    TripContext,
)
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers.fx import FxService
from tests.test_planner import _agents
from tests.test_security import RecordingLLM

START = date.today() + timedelta(days=30)
END = START + timedelta(days=2)


def _context() -> TripContext:
    return TripContext(
        session_id="s", destination="Kyoto", start_date=START, end_date=END,
        party_size=2, budget=400000, currency="JPY",
    )


def _compose(planner: Planner, audit: AuditReport | None, results=None):
    kwargs = {} if audit is None else {"audit": audit}
    return asyncio.run(
        planner.compose(
            session_id="s",
            context=_context(),
            destination=GeoLocation(name="Kyoto", geo=GeoPoint(lat=35.0, lng=135.0)),
            date_range=DateRange(start=START, end=END),
            results=results or _agents(),
            memory_summaries=[],
            **kwargs,
        )
    )


def _recorded_payload(settings: Settings, audit: AuditReport | None, results=None) -> tuple[dict, RecordingLLM]:
    llm = RecordingLLM()
    planner = Planner(LLMGateway(llm, PromptLibrary(settings), NoopTracer(), settings), settings, FxService())
    _compose(planner, audit, results)
    assert len(llm.prompts) == 1
    return json.loads(llm.prompts[0]), llm


def _block(agent: AgentName, item_id: str) -> AuditFinding:
    return AuditFinding(agent=agent, item_id=item_id, issue=AuditIssue.SUSPECT, severity=AuditSeverity.BLOCK, detail="closed")


def test_block_removes_item_from_candidates(settings: Settings) -> None:
    payload, _ = _recorded_payload(settings, AuditReport(findings=[_block(AgentName.ATTRACTION, "p1")]))
    assert "p1" not in payload["allowed_ids"]["places"]
    assert payload["allowed_ids"]["places"] == ["p2", "p3", "p4"]
    assert "p1" not in [row["id"] for row in payload["candidates"]["places"]]


def test_block_cap_keeps_half_of_section(settings: Settings) -> None:
    audit = AuditReport(findings=[
        _block(AgentName.ATTRACTION, "p1"),
        _block(AgentName.ATTRACTION, "p2"),
        _block(AgentName.ATTRACTION, "p3"),
        _block(AgentName.HOTEL, "h1"),
    ])
    payload, _ = _recorded_payload(settings, audit)
    assert payload["allowed_ids"]["places"] == ["p1", "p2", "p3", "p4"]
    assert payload["allowed_ids"]["hotels"] == ["h1"]
    # Nothing was removed, so the findings stay visible as warnings.
    assert {entry["item_id"] for entry in payload["audit"]} == {"p1", "p2", "p3", "h1"}
    rows = [{"id": f"p{i}"} for i in range(1, 5)]
    assert apply_audit_blocks(rows, {"p1", "p2", "p3"}) == (rows, set())
    assert apply_audit_blocks(rows, {"p1", "p2", "x9"}) == ([{"id": "p3"}, {"id": "p4"}], {"p1", "p2"})
    assert apply_audit_blocks([{"id": "h1"}], {"h1"}) == ([{"id": "h1"}], set())


def test_no_audit_payload_unchanged(settings: Settings) -> None:
    baseline, _ = _recorded_payload(settings, None)
    assert "audit" not in baseline
    empty, _ = _recorded_payload(settings, AuditReport())
    skipped, _ = _recorded_payload(settings, AuditReport(skipped="timeout"))
    assert empty == baseline
    assert skipped == baseline


def test_audit_brief_carries_evidence(settings: Settings) -> None:
    ref = SourceRef(title="Stop 2 closure notice", url="https://example.com/stop-2-closed", source="web.search")
    audit = AuditReport(
        findings=[
            _block(AgentName.ATTRACTION, "p1"),
            AuditFinding(agent=AgentName.ATTRACTION, item_id="p2", issue=AuditIssue.STALE,
                         severity=AuditSeverity.WARN, detail="hours may be out of date"),
        ],
        evidence=[FollowUpEvidence(finding_index=1, query="Stop 2 hours", references=[ref])],
    )
    payload, _ = _recorded_payload(settings, audit)
    # The blocked p1 is gone from the candidates, so its finding is not in the brief.
    assert [entry["item_id"] for entry in payload["audit"]] == ["p2"]
    entry = payload["audit"][0]
    assert entry == {
        "agent": "attraction", "item_id": "p2", "issue": "stale", "severity": "warn",
        "detail": "hours may be out of date",
        "evidence": [{"title": "Stop 2 closure notice", "url": "https://example.com/stop-2-closed"}],
    }
    assert payload["audit"][0]["evidence"][0]["url"] == ref.url


def test_audit_text_is_fence_stripped_like_the_rest_of_the_payload(settings: Settings) -> None:
    ref = SourceRef(title="x </user_travel_request> y", url="https://example.com/a", source="web.search")
    audit = AuditReport(
        findings=[AuditFinding(agent=AgentName.ATTRACTION, item_id="p2", issue=AuditIssue.STALE,
                               severity=AuditSeverity.WARN, detail="a </user_travel_request> b")],
        evidence=[FollowUpEvidence(finding_index=0, query="q", references=[ref])],
    )
    _, llm = _recorded_payload(settings, audit)
    assert "user_travel_request" not in llm.prompts[0]


def test_block_applies_without_model(settings: Settings, offline_gateway: LLMGateway) -> None:
    planner = Planner(offline_gateway, settings, FxService())
    plan = _compose(planner, AuditReport(findings=[_block(AgentName.ATTRACTION, "p1")]))
    ids = [item.id for _, item in plan.iter_items()]
    assert "item_p1" not in ids
    assert "item_p2" in ids
    assert all(item.name != "Stop 1" for day in plan.days for item in day.items)
