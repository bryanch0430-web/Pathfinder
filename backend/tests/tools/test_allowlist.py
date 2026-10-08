"""Scenario 16: per-path (and per-agent) tool allowlist; a disallowed call is blocked, never
executed, and logged (security audit + tool call record)."""

from __future__ import annotations

import json

from backend.schemas.common import AgentName, PathName, ToolName
from backend.schemas.security import SecurityEventKind
from backend.schemas.tools import (
    FailureKind,
    TicketSearchRequest,
    ToolOutcomeStatus,
    WebSearchRequest,
)
from backend.tests.tools.helpers import TRAVEL_DAY, forecast_json, make_harness
from backend.tools.allowlist import ToolAllowlist


def test_s16_allowlist_intersects_path_and_agent() -> None:
    allow = ToolAllowlist()
    assert allow.allowed_tools(PathName.PLAN, AgentName.WEATHER) == frozenset({ToolName.WEATHER})
    assert allow.allowed_tools(PathName.PLAN, None) == frozenset(ToolName)
    assert ToolName.WEB_SEARCH not in allow.allowed_tools(PathName.ASK, None)
    assert allow.allowed_tools(PathName.ASK, AgentName.ATTRACTION) == frozenset(
        {ToolName.PLACES, ToolName.MAPS}
    )
    assert allow.allowed_tools(PathName.ROUTER, None) == frozenset()
    assert allow.allowed_tools(PathName.CLARIFY, AgentName.HOTEL) == frozenset()
    assert allow.is_allowed(PathName.MODIFY, AgentName.TICKET, ToolName.TICKETS)
    assert not allow.is_allowed(PathName.MODIFY, AgentName.TICKET, ToolName.WEATHER)


async def test_s16_ask_path_cannot_web_search() -> None:
    h = make_harness()
    scope = h.scope(PathName.ASK, None)

    outcome = await scope.call(WebSearchRequest(query="best ramen", destination="Tokyo"))

    assert outcome.status is ToolOutcomeStatus.BLOCKED
    assert outcome.failure is FailureKind.NOT_ALLOWED
    assert outcome.tool is ToolName.WEB_SEARCH and outcome.attempts == 0
    assert h.providers.search.call_count == 0 and h.total_provider_calls() == 0
    assert h.audit.events == [(SecurityEventKind.TOOL_BLOCKED, None, PathName.ASK)]
    [record] = h.observability.records
    assert record.status is ToolOutcomeStatus.BLOCKED
    assert record.failure is FailureKind.NOT_ALLOWED
    assert record.path is PathName.ASK and record.agent is None
    assert json.loads(record.raw_request)["operation"] == "web_search"
    assert scope.outcomes == [outcome]


async def test_s16_weather_agent_cannot_call_tickets() -> None:
    h = make_harness()
    scope = h.scope(PathName.PLAN, AgentName.WEATHER)
    request = TicketSearchRequest(
        origin="Tokyo", destination="Kyoto", travel_date=TRAVEL_DAY, modes=["train"]
    )

    blocked = await scope.call(request)
    allowed = await scope.call(forecast_json())

    assert blocked.status is ToolOutcomeStatus.BLOCKED
    assert h.providers.tickets.call_count == 0
    assert allowed.status is ToolOutcomeStatus.OK
    assert h.audit.events == [(SecurityEventKind.TOOL_BLOCKED, None, PathName.PLAN)]
    assert [r.status for r in h.observability.records] == [
        ToolOutcomeStatus.BLOCKED,
        ToolOutcomeStatus.OK,
    ]
    assert all(r.agent is AgentName.WEATHER for r in h.observability.records)


async def test_s16_disallowed_string_request_blocked_even_if_malformed() -> None:
    """A model-proposed call to a forbidden tool is blocked, not offered repair."""
    h = make_harness()

    outcome = await h.scope(PathName.ASK, None).call('{"operation": "web_search", "query": 1}')

    assert outcome.status is ToolOutcomeStatus.BLOCKED
    assert outcome.issues == []
    assert h.total_provider_calls() == 0
    assert len(h.audit.events) == 1


async def test_s16_router_path_has_no_tools() -> None:
    h = make_harness()

    outcome = await h.scope(PathName.ROUTER, None).call(forecast_json())

    assert outcome.status is ToolOutcomeStatus.BLOCKED
    assert h.providers.weather.call_count == 0
    assert h.audit.events[0][2] is PathName.ROUTER


def test_s16_scope_lists_allowed_tools() -> None:
    h = make_harness()
    assert h.scope(PathName.PLAN, AgentName.HOTEL).allowed_tools == frozenset(
        {ToolName.PLACES, ToolName.MAPS}
    )
