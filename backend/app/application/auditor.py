"""One LLM call that checks the four pre-planning agents' results against the trip request.

The auditor reads what the agents gathered and returns typed findings. It never plans, ranks or
invents facts. Its output is sanitised before anything else sees it: a finding about an item no
agent returned is dropped, a follow-up query that carries a URL is dropped, and the follow-ups
that survive are capped, most severe first. The audit fails open: any failure becomes
``AuditReport(skipped=...)`` and the trip still plans. A canary leak is the one exception.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError

from app.application.llm_gateway import LLMGateway
from app.config import Settings
from app.domain.errors import CanaryLeak
from app.domain.models import (
    AgentName,
    AgentResult,
    AuditFinding,
    AuditReport,
    AuditSeverity,
    DateRange,
    TripContext,
)
from app.security import strip_fence_tags_deep

_URL_IN_QUERY = re.compile(r"(?i)https?://|www\.")
_SEVERITY_ORDER = {AuditSeverity.BLOCK: 0, AuditSeverity.WARN: 1, AuditSeverity.INFO: 2}


_QUERY_MIN, _QUERY_MAX, _DETAIL_MAX = 3, 160, 300


class _AuditDraft(BaseModel):
    """The model's raw reply. Deliberately lenient: the gateway re-asks the writer when this schema
    fails, so a too-long field must not fail it. Each finding is checked on its own in ``_sanitise``."""

    findings: list[Any]


def _parse_finding(raw: Any) -> AuditFinding | None:
    """One finding from the raw reply, or None when it cannot be made valid (only it is dropped)."""
    if not isinstance(raw, dict):
        return None
    data = dict(raw)
    detail = data.get("detail")
    if isinstance(detail, str):
        data["detail"] = detail[:_DETAIL_MAX]
    follow_up = data.get("follow_up")
    if isinstance(follow_up, dict):
        query = follow_up.get("query")
        query = query.strip() if isinstance(query, str) else ""
        ok = _QUERY_MIN <= len(query) <= _QUERY_MAX and not _URL_IN_QUERY.search(query)
        data["follow_up"] = {"query": query} if ok else None
    else:
        data["follow_up"] = None
    try:
        return AuditFinding.model_validate(data)
    except ValidationError:
        return None


def _rows(result: AgentResult | None, agent: AgentName) -> list[dict]:
    """The agent's candidate rows, in the shapes the planner reads (planner.py ``_draft``)."""
    payload = result.payload if result else {}
    if agent == AgentName.ATTRACTION:
        keys = ("places", "meals")
    elif agent == AgentName.HOTEL:
        keys = ("hotels",)
    elif agent == AgentName.WEATHER:
        keys = ("days",)
    else:
        keys = ("flights",)
    rows: list[dict] = []
    for key in keys:
        rows.extend(row for row in (payload.get(key) or []) if isinstance(row, dict))
    return rows


def _row_id(row: dict, agent: AgentName) -> str | None:
    value = row.get("date") if agent == AgentName.WEATHER else row.get("id")
    return None if value is None else str(value)


def _known_ids(results: dict[str, AgentResult]) -> dict[AgentName, set[str]]:
    known: dict[AgentName, set[str]] = {}
    for agent in AgentName:
        ids = (_row_id(row, agent) for row in _rows(results.get(agent.value), agent))
        known[agent] = {value for value in ids if value}
    return known


def _price(row: dict) -> Any:
    price = row.get("nightly_price") or row.get("price")
    if isinstance(price, dict):
        return {"amount": price.get("amount"), "currency": price.get("currency")}
    return price


def _hours(value: Any) -> list:
    if isinstance(value, str):
        return [value] if value else []
    return value[:2] if isinstance(value, list) else []


def _item(row: dict, agent: AgentName) -> dict:
    if agent == AgentName.WEATHER:
        day = _row_id(row, agent)
        return {
            "id": day, "date": day, "status": row.get("status"), "summary": row.get("summary"),
            "high_c": row.get("high_c"), "low_c": row.get("low_c"),
        }
    name = row.get("name")
    if name is None and agent == AgentName.TICKET:
        name = " ".join(str(row.get(key) or "") for key in ("airline", "flight_number")).strip() or None
    return {
        "id": _row_id(row, agent),
        "name": name,
        "category": row.get("category") or row.get("area"),
        "rating": row.get("rating"),
        "review_count": row.get("review_count"),
        "address": row.get("address"),
        "opening_hours": _hours(row.get("opening_hours")),
        "price": _price(row),
        "source_titles": [
            str(ref.get("title") or "") for ref in (row.get("source_refs") or []) if isinstance(ref, dict)
        ][:3],
    }


def _audit_payload(
    results: dict[str, AgentResult],
    context: TripContext,
    date_range: DateRange,
    settings: Settings,
) -> dict:
    agents: dict[str, dict] = {}
    for agent in AgentName:
        result = results.get(agent.value)
        rows = _rows(result, agent)[: settings.audit_max_items_per_agent]
        agents[agent.value] = {
            "status": result.status.value if result else "missing",
            "failures": [
                {"section": gap.section, "kind": gap.kind.value, "message": gap.message[:200]}
                for gap in (result.failures if result else [])
            ],
            "items": [_item(row, agent) for row in rows],
        }
    return {
        "trip": {
            "destination": context.destination,
            "origin": context.origin,
            "start": date_range.start.isoformat(),
            "end": date_range.end.isoformat(),
            "days": len(date_range.dates),
            "party_size": context.party_size,
            "budget": context.budget,
            "currency": context.currency,
            "hotel_style": context.hotel_style,
            "pace": context.pace,
            "preferences": context.preferences,
            "hard_constraints": context.hard_constraints,
        },
        "agents": agents,
    }


def _sanitise(
    raw_findings: list[Any],
    known: dict[AgentName, set[str]],
    settings: Settings,
) -> list[AuditFinding]:
    kept: list[AuditFinding] = []
    for raw in raw_findings:
        finding = _parse_finding(raw)
        if finding is None:
            continue
        if finding.item_id is not None and finding.item_id not in known.get(finding.agent, set()):
            continue
        kept.append(finding)
    # Follow-ups are capped; the most severe findings keep theirs (stable within a severity).
    wanting = sorted(
        (i for i, f in enumerate(kept) if f.follow_up is not None),
        key=lambda i: _SEVERITY_ORDER[kept[i].severity],
    )
    allowed = set(wanting[: max(settings.audit_max_follow_ups, 0)])
    return [
        f if f.follow_up is None or i in allowed else f.model_copy(update={"follow_up": None})
        for i, f in enumerate(kept)
    ]


async def audit_results(
    *,
    results: dict[str, AgentResult],
    context: TripContext,
    date_range: DateRange,
    gateway: LLMGateway,
    settings: Settings,
) -> AuditReport:
    if not gateway.available:
        return AuditReport(skipped="no model")
    payload = _audit_payload(results, context, date_range, settings)
    user = json.dumps(strip_fence_tags_deep(payload), ensure_ascii=False, default=str)
    try:
        data = await asyncio.wait_for(
            gateway.json("audit", user=user, schema=_AuditDraft, purpose="preplan.audit"),
            settings.audit_timeout_seconds,
        )
        draft = _AuditDraft.model_validate(data)
    except CanaryLeak:
        raise
    except Exception as exc:
        return AuditReport(skipped=f"audit failed: {type(exc).__name__}")
    return AuditReport(findings=_sanitise(draft.findings, _known_ids(results), settings))
