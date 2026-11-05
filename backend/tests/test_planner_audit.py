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


# --- final-review fixes ------------------------------------------------------

def _compose_with(settings: Settings, audit: AuditReport | None, *, results=None, context=None, llm=None, **kwargs):
    llm = llm or RecordingLLM()
    planner = Planner(LLMGateway(llm, PromptLibrary(settings), NoopTracer(), settings), settings, FxService())
    extra = {} if audit is None else {"audit": audit}
    plan = asyncio.run(
        planner.compose(
            session_id="s",
            context=context or _context(),
            destination=GeoLocation(name="Kyoto", geo=GeoPoint(lat=35.0, lng=135.0)),
            date_range=DateRange(start=START, end=END),
            results=results or _agents(),
            memory_summaries=[],
            **extra,
            **kwargs,
        )
    )
    return plan, json.loads(llm.prompts[0]) if llm.prompts else None


def test_a_blocked_wish_is_kept_and_its_finding_stays_a_warning(settings: Settings) -> None:
    """A place the traveller named is never dropped by a block; the constraint does not fail falsely."""
    results = _agents()
    results["attraction"].payload["wishes"] = {"Stop 1": "Stop 1"}
    plan, payload = _compose_with(settings, AuditReport(findings=[_block(AgentName.ATTRACTION, "p1")]), results=results)
    assert "p1" in payload["allowed_ids"]["places"]
    assert [(entry["item_id"], entry["severity"]) for entry in payload["audit"]] == [("p1", "block")]
    check = next(check for check in plan.constraints if check.text == "Include Stop 1")
    assert str(check.status) == "pass", check.evidence
    assert any(item.name == "Stop 1" for day in plan.days for item in day.items)


def test_a_blocked_must_see_is_kept(settings: Settings) -> None:
    context = _context().model_copy(update={"preferences": ["must-see: Stop 1"]})
    _, payload = _compose_with(settings, AuditReport(findings=[_block(AgentName.ATTRACTION, "p1")]), context=context)
    assert "p1" in payload["allowed_ids"]["places"]
    assert [entry["item_id"] for entry in payload["audit"]] == ["p1"]


def test_an_unnamed_block_still_drops_alongside_a_named_one(settings: Settings) -> None:
    context = _context().model_copy(update={"preferences": ["must-see: Stop 1"]})
    audit = AuditReport(findings=[_block(AgentName.ATTRACTION, "p1"), _block(AgentName.ATTRACTION, "p2")])
    _, payload = _compose_with(settings, audit, context=context)
    assert payload["allowed_ids"]["places"] == ["p1", "p3", "p4"]


def test_the_kept_hotel_is_exempt_from_a_hotel_block(settings: Settings) -> None:
    from app.domain.models import HotelStay

    results = _agents()
    second = dict(results["hotel"].payload["hotels"][0], id="h2", name="Hotel Two")
    results["hotel"].payload["hotels"].append(second)
    keep = HotelStay(name="Hotel One", check_in=START, check_out=END)
    plan, payload = _compose_with(
        settings, AuditReport(findings=[_block(AgentName.HOTEL, "h1")]), results=results, keep_hotel=keep
    )
    assert payload["allowed_ids"]["hotels"] == ["h1", "h2"]
    assert plan.hotels[0].name == "Hotel One"
    assert plan.hotels[0].price_per_night is not None
    # Control: without a kept hotel the same block drops h1.
    _, payload = _compose_with(settings, AuditReport(findings=[_block(AgentName.HOTEL, "h1")]), results=_agents_two_hotels())
    assert payload["allowed_ids"]["hotels"] == ["h2"]


def _agents_two_hotels():
    results = _agents()
    second = dict(results["hotel"].payload["hotels"][0], id="h2", name="Hotel Two")
    results["hotel"].payload["hotels"].append(second)
    return results


class _ReplyLLM(RecordingLLM):
    def __init__(self, reply: dict) -> None:
        super().__init__()
        self.reply = json.dumps(reply)

    async def complete(self, *, system, user, purpose, json_mode=True, temperature=0.2, max_tokens=4096):
        from app.infrastructure.llm import LLMResult

        self.prompts.append(user)
        return LLMResult(text=self.reply, model="reply")


EVIDENCE_URL = "https://example.com/stop-2-closed"
OTHER_URL = "https://other.example/kyoto-guide"


def _evidence_audit() -> AuditReport:
    ref = SourceRef(title="Stop 2 closure notice", url=EVIDENCE_URL, source="web.search")
    return AuditReport(
        findings=[AuditFinding(agent=AgentName.ATTRACTION, item_id="p2", issue=AuditIssue.STALE,
                               severity=AuditSeverity.WARN, detail="hours may be out of date")],
        evidence=[FollowUpEvidence(finding_index=0, query="Stop 2 hours", references=[ref])],
    )


def _url_reply() -> dict:
    return {
        "summary": f"Three days in Kyoto (see {EVIDENCE_URL}).",
        "notes": ["Stop 2 may be closed: HTTPS://EXAMPLE.COM/stop-2-closed.", f"Guide: {OTHER_URL}"],
    }


def _all_text(plan) -> str:
    return " ".join([plan.summary, *plan.data_quality.notes]).lower()


def test_evidence_urls_stay_out_of_the_notes_and_summary(settings: Settings) -> None:
    plan, payload = _compose_with(settings, _evidence_audit(), llm=_ReplyLLM(_url_reply()))
    assert payload["audit"][0]["evidence"][0]["url"] == EVIDENCE_URL
    assert EVIDENCE_URL not in _all_text(plan)
    # Control: a URL that is not audit evidence is left alone.
    assert OTHER_URL in " ".join(plan.data_quality.notes)
    assert plan.summary.startswith("Three days in Kyoto")


def test_without_evidence_the_notes_and_summary_are_untouched(settings: Settings) -> None:
    reply = _url_reply()
    plan, _ = _compose_with(settings, None, llm=_ReplyLLM(reply))
    assert plan.summary == reply["summary"]
    assert [note for note in plan.data_quality.notes if note in reply["notes"]] == reply["notes"]


def test_audit_brief_is_capped_with_blocks_first(settings: Settings) -> None:
    from app.application.planner import _audit_brief

    findings = []
    for index in range(10):
        findings.append(AuditFinding(agent=AgentName.ATTRACTION, item_id=None, issue=AuditIssue.STALE,
                                     severity=AuditSeverity.INFO, detail=f"info {index}"))
        findings.append(AuditFinding(agent=AgentName.ATTRACTION, item_id=None, issue=AuditIssue.STALE,
                                     severity=AuditSeverity.WARN, detail=f"warn {index}"))
        findings.append(AuditFinding(agent=AgentName.ATTRACTION, item_id=None, issue=AuditIssue.SUSPECT,
                                     severity=AuditSeverity.BLOCK, detail=f"block {index}"))
    brief = _audit_brief(AuditReport(findings=findings), set())
    assert len(brief) == 20
    assert [entry["detail"] for entry in brief] == (
        [f"block {i}" for i in range(10)] + [f"warn {i}" for i in range(10)]
    )
