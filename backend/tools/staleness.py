"""Timestamps and staleness.

Proposal: "each tool result carries a timestamp, and an item older than a set limit is marked for
refresh. On the modify path, only the agent for the failed or stale item is called again."
Limits differ per section because the data ages at different speeds (ticket availability in
an hour, weather in hours, hotels in a day, attractions in a week).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.schemas.common import ALL_AGENTS, AgentName, SectionStatus, utcnow
from backend.schemas.trip_plan import SectionState, TripPlan
from backend.settings import Settings


def stale_limit(agent: AgentName, settings: Settings) -> timedelta:
    """Per-section limit from settings (weather / ticket / hotel / attraction)."""
    seconds = {
        AgentName.WEATHER: settings.stale_after_weather_s,
        AgentName.TICKET: settings.stale_after_ticket_s,
        AgentName.HOTEL: settings.stale_after_hotel_s,
        AgentName.ATTRACTION: settings.stale_after_attraction_s,
    }[agent]
    return timedelta(seconds=seconds)


def _aware(moment: datetime) -> datetime:
    # A naive timestamp is taken to be UTC (every timestamp we produce is aware UTC).
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def is_stale(
    fetched_at: datetime | None, agent: AgentName, settings: Settings, now: datetime | None = None
) -> bool:
    """True when fetched_at is None or older than the agent's limit."""
    if fetched_at is None:
        return True
    current = _aware(now) if now is not None else utcnow()
    return current - _aware(fetched_at) > stale_limit(agent, settings)


def _section_is_stale(section: SectionState, settings: Settings, now: datetime | None) -> bool:
    if section.status is SectionStatus.STALE:
        return True
    return section.status is SectionStatus.OK and is_stale(
        section.fetched_at, section.agent, settings, now
    )


def mark_stale_sections(plan: TripPlan, settings: Settings, now: datetime | None = None) -> TripPlan:
    """Return a copy of `plan` whose OK sections older than their limit have status STALE.
    Unavailable sections are left as they are. Never mutates the input."""
    now = now if now is not None else utcnow()
    sections: list[SectionState] = []
    for section in plan.sections:
        if section.status is SectionStatus.OK and is_stale(
            section.fetched_at, section.agent, settings, now
        ):
            limit = stale_limit(section.agent, settings)
            sections.append(
                section.model_copy(
                    update={
                        "status": SectionStatus.STALE,
                        "reason": f"older than the {int(limit.total_seconds())}s limit; marked for refresh",
                    }
                )
            )
        else:
            sections.append(section.model_copy(deep=True))
    return plan.model_copy(deep=True, update={"sections": sections})


def sections_needing_refresh(
    plan: TripPlan, settings: Settings, now: datetime | None = None
) -> list[AgentName]:
    """Agents whose section is STALE (by status or by age) or UNAVAILABLE, in ALL_AGENTS order.
    These are re-run on the next modify turn. An agent with no section state at all (it was not
    run for this plan, e.g. no origin for tickets) is not listed."""
    now = now if now is not None else utcnow()
    needing: list[AgentName] = []
    for agent in ALL_AGENTS:
        section = plan.section(agent)
        if section is None:
            continue
        if section.status is SectionStatus.UNAVAILABLE or _section_is_stale(section, settings, now):
            needing.append(agent)
    return needing
