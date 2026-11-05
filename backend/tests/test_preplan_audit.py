"""Audit report models and the off-by-default pre-planning audit settings."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.domain.models import (
    AgentName,
    AuditFinding,
    AuditFollowUp,
    AuditIssue,
    AuditReport,
    AuditSeverity,
)


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
