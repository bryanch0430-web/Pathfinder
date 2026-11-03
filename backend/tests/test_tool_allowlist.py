"""Proposal DB-05 (p2, l.78-79): each path may call only the tools on its allowlist.

The Toolbox is the choke point every provider call goes through. These tests call
tools that are off a path's allowlist and assert they are refused, logged as
blocked, and never executed.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest

from app.application.agents import AgentContext, AttractionAgent
from app.application.toolbox import PATH_ALLOWLIST, Toolbox
from app.config import Settings
from app.domain.errors import ProviderError, SecurityBlocked
from app.domain.models import DateRange, GeoLocation, GeoPoint, TripContext
from app.evaluation import SandboxRegistry
from app.infrastructure.observability import NoopTracer

# Every tool any path owns, plus tools no path owns (booking, payment, mail, shell).
FOREIGN_TOOLS = {"book_flight", "cancel_order", "payments.charge", "email.send", "shell.exec"}
ALL_TOOLS = sorted(set().union(*PATH_ALLOWLIST.values()) | FOREIGN_TOOLS)


class RecordingStore:
    """Stands in for SQLiteStore.log_tool_call so the test can read the tool log."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def log_tool_call(self, **row) -> None:
        self.calls.append(row)


def _toolbox(path: str, store: RecordingStore) -> Toolbox:
    return Toolbox(path=path, tracer=NoopTracer(), store=store, request_id=f"req_db05_{path}")


@pytest.mark.parametrize("path", sorted(PATH_ALLOWLIST))
def test_db05_each_path_refuses_every_tool_off_its_allowlist(path: str) -> None:
    """Proposal DB-05 (p2): a tool outside the path's allowlist is refused, logged and never run."""
    store = RecordingStore()
    toolbox = _toolbox(path, store)
    ran: list[str] = []

    async def tool(**kwargs):
        ran.append(kwargs["name"])
        return "ran"

    refused = [name for name in ALL_TOOLS if name not in PATH_ALLOWLIST[path]]
    assert refused, "every path must have at least one tool it may not call"
    for name in refused:
        with pytest.raises(SecurityBlocked) as caught:
            asyncio.run(toolbox.call(name, tool, name=name))
        assert caught.value.category == "tool_allowlist"
    assert ran == []
    assert toolbox.calls == 0
    assert [row["tool"] for row in store.calls] == refused
    assert all(row["status"] == "blocked" and row["source"] == "allowlist" for row in store.calls)


@pytest.mark.parametrize("path", sorted(PATH_ALLOWLIST))
def test_db05_each_path_runs_the_tools_on_its_allowlist(path: str) -> None:
    """Proposal DB-05 (p2): an allowlisted tool runs and is logged as ok."""
    store = RecordingStore()
    toolbox = _toolbox(path, store)

    async def tool(**kwargs):
        return kwargs["name"]

    allowed = sorted(PATH_ALLOWLIST[path])
    for name in allowed:
        assert asyncio.run(toolbox.call(name, tool, name=name)) == name
    assert toolbox.calls == len(allowed)
    assert [row["status"] for row in store.calls] == ["ok"] * len(allowed)


def test_db05_paths_own_different_tools() -> None:
    """Proposal DB-05 (p2): the quick-question path cannot book or search hotels; clarify has no tools."""
    assert PATH_ALLOWLIST["clarify"] == frozenset()
    assert "hotels.search" not in PATH_ALLOWLIST["ask"]
    assert "flights.search" not in PATH_ALLOWLIST["ask"]
    assert "web.search" in PATH_ALLOWLIST["ask"]
    assert not FOREIGN_TOOLS & set().union(*PATH_ALLOWLIST.values())


def test_db05_unknown_path_gets_no_tools() -> None:
    """Proposal DB-05 (p2): a path name that has no allowlist cannot call anything."""
    store = RecordingStore()
    toolbox = _toolbox("admin", store)

    async def tool(**_):
        return "ran"

    for name in ALL_TOOLS:
        with pytest.raises(SecurityBlocked):
            asyncio.run(toolbox.call(name, tool))
    assert toolbox.allowed == frozenset()
    assert len(store.calls) == len(ALL_TOOLS)


def _agent_request(settings: Settings, toolbox: Toolbox) -> AgentContext:
    start = date.today() + timedelta(days=30)
    return AgentContext(
        context=TripContext(session_id="db05", destination="Kyoto", origin="Hong Kong",
                            start_date=start, end_date=start + timedelta(days=2), party_size=2, budget=3000),
        destination=GeoLocation(name="Kyoto", country_code="JP", timezone="Asia/Tokyo",
                                geo=GeoPoint(lat=35.01, lng=135.77), source="sandbox"),
        date_range=DateRange(start=start, end=start + timedelta(days=2)),
        request_id="req_db05_agent",
        toolbox=toolbox,
        settings=settings,
    )


def test_db05_agent_refuses_a_tool_outside_its_own_set(settings: Settings) -> None:
    """Proposal DB-05 (p2): an agent on an allowed path still may not call a tool it does not own."""
    store = RecordingStore()
    toolbox = _toolbox("plan", store)
    agent = AttractionAgent(SandboxRegistry(settings, "Kyoto"), settings)

    async def tool(**_):
        raise AssertionError("must not run")

    with pytest.raises(ProviderError):
        asyncio.run(agent._call(_agent_request(settings, toolbox), "hotels.search", tool))
    assert store.calls == [] and toolbox.calls == 0


class _NoPlaces:
    name = "sandbox-empty-places"

    async def search(self, **_):
        return []


class _SpyWeb:
    """A web search backend that records every call made to it."""

    name = "sandbox-spy-web"

    def __init__(self) -> None:
        self.calls = 0

    async def search(self, **_):
        self.calls += 1
        return []


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Proposal DB-05: plan/modify agents call web search directly "
        "(agents.py BaseAgent._web_references -> web_lookup.lookup_web_references), bypassing "
        "the Toolbox allowlist and tool log; web.search is not on the plan/modify allowlists"
    ),
)
def test_db05_plan_path_web_lookups_go_through_the_allowlist(settings: Settings) -> None:
    """Proposal DB-05 (p2): every web search made on the plan path passes the plan allowlist."""
    registry = SandboxRegistry(settings, "Kyoto")
    registry.places = [_NoPlaces()]
    spy = _SpyWeb()
    registry.web = [spy]
    store = RecordingStore()
    toolbox = _toolbox("plan", store)
    asyncio.run(AttractionAgent(registry, settings).run(_agent_request(settings, toolbox)))
    through_toolbox = [row for row in store.calls if row["tool"] == "web.search" and row["status"] != "blocked"]
    assert spy.calls == len(through_toolbox)
