"""Scenario 13: every tool result is timestamped; sections older than their limit are marked
for refresh, and only those agents are re-run."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.schemas.common import AgentName, SectionStatus
from backend.schemas.tools import ToolOutcomeStatus
from backend.schemas.trip_plan import DayPlan, SectionState, TripPlan
from backend.tests.tools.helpers import TRAVEL_DAY, forecast_json, make_harness, make_settings
from backend.tools.staleness import (
    is_stale,
    mark_stale_sections,
    sections_needing_refresh,
    stale_limit,
)

NOW = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)


def _plan(sections: list[SectionState]) -> TripPlan:
    return TripPlan(
        plan_id="plan-1",
        destination="Kyoto",
        start_date=TRAVEL_DAY,
        end_date=TRAVEL_DAY,
        party_size=2,
        days=[DayPlan(date=TRAVEL_DAY)],
        sections=sections,
    )


async def test_s13_ok_outcome_is_timestamped_utc() -> None:
    h = make_harness()
    before = datetime.now(UTC)

    outcome = await h.scope().call(forecast_json())

    assert outcome.status is ToolOutcomeStatus.OK
    assert outcome.fetched_at is not None
    assert outcome.fetched_at.utcoffset() == timedelta(0)
    assert before <= outcome.fetched_at <= datetime.now(UTC)
    [record] = h.observability.records
    assert record.fetched_at == outcome.fetched_at
    assert record.payload == outcome.payload
    assert record.provider == "mock-weather"
    assert record.started_at <= record.ended_at


def test_s13_stale_limits_come_from_settings() -> None:
    s = make_settings(stale_after_weather_s=60, stale_after_ticket_s=30)
    assert stale_limit(AgentName.WEATHER, s) == timedelta(seconds=60)
    assert stale_limit(AgentName.TICKET, s) == timedelta(seconds=30)
    assert stale_limit(AgentName.HOTEL, s) == timedelta(seconds=s.stale_after_hotel_s)
    assert stale_limit(AgentName.ATTRACTION, s) == timedelta(seconds=s.stale_after_attraction_s)


def test_s13_is_stale() -> None:
    s = make_settings()
    weather_limit = timedelta(seconds=s.stale_after_weather_s)
    assert is_stale(None, AgentName.WEATHER, s, now=NOW)
    assert not is_stale(NOW - weather_limit, AgentName.WEATHER, s, now=NOW)  # exactly at limit
    assert is_stale(NOW - weather_limit - timedelta(seconds=1), AgentName.WEATHER, s, now=NOW)
    assert not is_stale(NOW - timedelta(hours=7), AgentName.HOTEL, s, now=NOW)
    # Defaults to the real clock.
    assert not is_stale(datetime.now(UTC), AgentName.TICKET, s)


def test_s13_mark_stale_sections_and_refresh_list() -> None:
    s = make_settings()
    plan = _plan(
        [
            SectionState(agent=AgentName.ATTRACTION, status=SectionStatus.OK, fetched_at=NOW - timedelta(days=1)),
            SectionState(agent=AgentName.HOTEL, status=SectionStatus.OK, fetched_at=NOW - timedelta(hours=1)),
            SectionState(agent=AgentName.WEATHER, status=SectionStatus.OK, fetched_at=NOW - timedelta(hours=7)),
            SectionState(agent=AgentName.TICKET, status=SectionStatus.UNAVAILABLE, reason="provider down"),
        ]
    )
    original = plan.model_dump()

    marked = mark_stale_sections(plan, s, now=NOW)

    assert plan.model_dump() == original  # input not mutated
    statuses = {sec.agent: sec.status for sec in marked.sections}
    assert statuses == {
        AgentName.ATTRACTION: SectionStatus.OK,
        AgentName.HOTEL: SectionStatus.OK,
        AgentName.WEATHER: SectionStatus.STALE,
        AgentName.TICKET: SectionStatus.UNAVAILABLE,  # left as is
    }
    weather = marked.section(AgentName.WEATHER)
    assert weather is not None and weather.fetched_at == NOW - timedelta(hours=7)
    # Only the weather (stale) and ticket (unavailable) agents are re-run, in ALL_AGENTS order.
    assert sections_needing_refresh(plan, s, now=NOW) == [AgentName.WEATHER, AgentName.TICKET]
    assert sections_needing_refresh(marked, s, now=NOW) == [AgentName.WEATHER, AgentName.TICKET]


def test_s13_explicit_stale_status_and_missing_timestamp() -> None:
    s = make_settings()
    plan = _plan(
        [
            SectionState(agent=AgentName.HOTEL, status=SectionStatus.STALE, fetched_at=NOW),
            SectionState(agent=AgentName.ATTRACTION, status=SectionStatus.OK, fetched_at=None),
        ]
    )
    assert sections_needing_refresh(plan, s, now=NOW) == [AgentName.ATTRACTION, AgentName.HOTEL]
    assert mark_stale_sections(plan, s, now=NOW).section(AgentName.ATTRACTION).status is SectionStatus.STALE  # type: ignore[union-attr]


def test_s13_fresh_plan_needs_nothing() -> None:
    s = make_settings()
    plan = _plan([SectionState(agent=a, status=SectionStatus.OK, fetched_at=NOW) for a in AgentName])
    assert sections_needing_refresh(plan, s, now=NOW) == []
    assert mark_stale_sections(plan, s, now=NOW) == plan
