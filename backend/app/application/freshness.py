"""Outdated forecasts, fares and hotel prices are re-fetched by their own agent (Proposal DB-13).

Time-sensitive sections carry the time they were fetched. On a later turn, and when
a saved plan is reopened, each section's oldest stamp is compared with its window
(``Settings.weather_fresh_seconds``, ``ticket_fresh_seconds``, ``hotel_fresh_seconds``).
An outdated section is marked on the plan (``data_quality.freshness``) and fetched
again by the agent that owns it, through the Toolbox ``refresh`` path (allowlisted and
logged). Every other section of the plan is left as it was.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.application.agents import AgentContext, BaseAgent, HotelAgent, TicketAgent, WeatherAgent
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.models import (
    FreshnessMark,
    GeoLocation,
    Money,
    PriceKind,
    SourceRef,
    TripContext,
    TripPlan,
    WeatherDay,
    new_id,
    utcnow,
)
from app.domain.ports import Tracer
from app.infrastructure.observability import start_trace

logger = logging.getLogger(__name__)

SECTIONS = ("weather", "tickets", "hotels")
LABELS = {"weather": "weather forecast", "tickets": "flight fares", "hotels": "hotel prices"}


class _OwnSourceOnly:
    """A refresh asks the section's own source again; it never falls back to web pages."""

    async def _web_references(self, **_: Any) -> tuple[list[dict[str, Any]], None]:
        return [], None


class _WeatherRefresh(_OwnSourceOnly, WeatherAgent):
    pass


class _TicketRefresh(_OwnSourceOnly, TicketAgent):
    pass


class _HotelRefresh(_OwnSourceOnly, HotelAgent):
    pass


AGENTS: dict[str, type[BaseAgent]] = {
    "weather": _WeatherRefresh,
    "tickets": _TicketRefresh,
    "hotels": _HotelRefresh,
}


def window_seconds(section: str, settings: Settings) -> int:
    return {
        "weather": settings.weather_fresh_seconds,
        "tickets": settings.ticket_fresh_seconds,
        "hotels": settings.hotel_fresh_seconds,
    }[section]


def section_stamps(plan: TripPlan) -> dict[str, datetime]:
    """The oldest fetch time of each time-sensitive section that holds data."""
    stamps = {
        "weather": [day.weather.fetched_at for day in plan.days if day.weather is not None],
        "tickets": [ticket.fetched_at for ticket in plan.tickets if ticket.mode == "flight"],
        "hotels": [hotel.fetched_at for hotel in plan.hotels],
    }
    return {section: min(_aware(stamp) for stamp in values) for section, values in stamps.items() if values}


def outdated_sections(plan: TripPlan, settings: Settings, now: datetime) -> dict[str, datetime]:
    """Sections older than their window. Nothing about a trip that has ended is refreshed."""
    if plan.dates.end < now.date():
        return {}
    return {
        section: stamp
        for section, stamp in section_stamps(plan).items()
        if (now - stamp).total_seconds() > window_seconds(section, settings)
    }


@dataclass
class RefreshOutcome:
    plan: TripPlan
    outdated: list[str]
    refreshed: list[str]


class FreshnessService:
    def __init__(
        self,
        *,
        registry: Any,
        planner: Any,
        settings: Settings,
        store: Any | None = None,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.registry = registry
        self.planner = planner
        self.settings = settings
        self.store = store
        self.clock = clock

    def needs_check(self, plan: TripPlan) -> bool:
        now = _aware(self.clock())
        outdated = outdated_sections(plan, self.settings, now)
        cleared = any(
            mark.status == "outdated" and mark.section not in outdated for mark in plan.data_quality.freshness
        )
        return bool(outdated) or cleared

    async def check(
        self,
        plan: TripPlan,
        *,
        context: TripContext | None,
        tracer: Tracer,
        request_id: str,
    ) -> RefreshOutcome | None:
        """Mark and re-fetch outdated sections. None when every section is in date.

        A plan in which a section was re-fetched is saved as the next version.
        """
        if not self.needs_check(plan):
            return None
        now = _aware(self.clock())
        outdated = outdated_sections(plan, self.settings, now)
        updated = plan.model_copy(deep=True)
        toolbox = Toolbox(path="refresh", tracer=tracer, store=self.store, request_id=request_id)
        request_context = self._agent_context(updated, context)
        with tracer.span("freshness", outdated=sorted(outdated)) as span:
            sections = list(outdated)
            prices_before = _prices(updated)
            notes = await asyncio.gather(
                *(self._refetch(section, updated, request_context, toolbox, now) for section in sections)
            )
            marks = {
                mark.section: mark
                for mark in updated.data_quality.freshness
                if not (mark.status == "outdated" and mark.section not in outdated)
            }
            refreshed: list[str] = []
            for section, note in zip(sections, notes):
                if note is None:
                    refreshed.append(section)
                marks[section] = FreshnessMark(
                    section=section,  # type: ignore[arg-type]
                    status="refreshed" if note is None else "outdated",
                    fetched_at=outdated[section],
                    checked_at=now,
                    window_seconds=window_seconds(section, self.settings),
                    note=note or "",
                )
            updated.data_quality.freshness = [marks[section] for section in SECTIONS if section in marks]
            if _prices(updated) != prices_before:
                updated.cost = self.planner._cost(
                    request_context,
                    updated.hotels[0] if updated.hotels else None,
                    updated.tickets,
                    updated.dates,
                )
            span.attrs["refreshed"] = refreshed
        if refreshed:
            updated.version = plan.version + 1
            updated.updated_at = utcnow()
            updated.change_summary = "Refreshed the outdated " + _join([LABELS[section] for section in refreshed]) + "."
            if self.store is not None:
                self.store.save_plan(updated, updated.change_summary)
        return RefreshOutcome(plan=updated, outdated=sorted(outdated), refreshed=refreshed)

    async def on_reopen(self, plan: TripPlan) -> TripPlan:
        """A saved plan opened again: the same check as a later turn, under its own request id."""
        if plan is None or not self.needs_check(plan):
            return plan
        request_id = new_id("req")
        tracer = start_trace(self.settings, request_id)
        context = self.store.get_context(plan.session_id) if self.store is not None else None
        try:
            outcome = await self.check(plan, context=context, tracer=tracer, request_id=request_id)
        except Exception:  # a failed refresh never stops the plan from opening
            logger.exception("refreshing an outdated plan failed")
            tracer.finish(status="error")
            return plan
        tracer.finish(status="ok")
        return outcome.plan if outcome is not None else plan

    def _agent_context(self, plan: TripPlan, context: TripContext | None) -> TripContext:
        """The trip as the plan states it: its dates, origin and party size."""
        base = context or TripContext(
            session_id=plan.session_id,
            budget=plan.budget.amount if plan.budget else None,
            currency=plan.cost.currency or self.settings.default_currency,
        )
        return base.model_copy(
            update={
                "destination": base.destination or plan.destination.name,
                "origin": plan.origin or base.origin,
                "start_date": plan.dates.start,
                "end_date": plan.dates.end,
                "days": len(plan.dates.dates),
                "party_size": plan.party_size,
            }
        )

    async def _refetch(
        self,
        section: str,
        plan: TripPlan,
        context: TripContext,
        toolbox: Toolbox,
        now: datetime,
    ) -> str | None:
        """Re-run the section's own agent and apply what it returned. None when refreshed,
        otherwise why the earlier data was kept."""
        if plan.destination.geo is None:
            return "The destination has no coordinates, so the source could not be asked again."
        destination = GeoLocation(
            name=plan.destination.name,
            country_code=plan.destination.country_code,
            timezone=plan.destination.timezone,
            geo=plan.destination.geo,
        )
        agent = AGENTS[section](self.registry, self.settings)
        request = AgentContext(
            context=context,
            destination=destination,
            date_range=plan.dates,
            request_id=toolbox.request_id,
            toolbox=toolbox,
            settings=self.settings,
        )
        try:
            result = await agent.run(request)
        except Exception as exc:
            logger.warning("refreshing %s failed: %s", section, exc)
            return "The source could not be reached; the earlier data is kept."
        apply = {"weather": _apply_weather, "tickets": _apply_fares, "hotels": _apply_hotels}[section]
        return apply(plan, result.payload, now)


def _apply_weather(plan: TripPlan, payload: dict[str, Any], now: datetime) -> str | None:
    rows = {row.get("date"): row for row in payload.get("days", []) if row.get("date")}
    kept = 0
    for day in plan.days:
        row = rows.get(day.date.isoformat())
        fresh = WeatherDay.model_validate(row) if row else None
        if fresh is None or (day.weather is not None and day.weather.is_available and not fresh.is_available):
            if day.weather is not None:
                kept += 1
            continue
        fresh.fetched_at = now
        day.weather = fresh
    if kept:
        return f"No new forecast for {kept} of {len(plan.days)} days; the earlier forecast is kept."
    return None


def _apply_fares(plan: TripPlan, payload: dict[str, Any], now: datetime) -> str | None:
    rows: dict[str, dict[str, Any]] = {}
    for row in payload.get("flights", []):
        name = " ".join(part for part in [row.get("airline"), row.get("flight_number")] if part) or "Flight"
        rows.setdefault(name.casefold(), row)
    kept = 0
    for ticket in plan.tickets:
        if ticket.mode != "flight":
            continue
        row = rows.get(ticket.name.casefold())
        if row is None or (ticket.price is not None and not row.get("price")):
            kept += 1
            continue
        if row.get("price"):
            ticket.price = Money.model_validate(row["price"])
            ticket.price_kind = PriceKind(row.get("price_kind", "reference"))
        ticket.source_refs = [SourceRef.model_validate(ref) for ref in row.get("source_refs", [])] or ticket.source_refs
        ticket.booking_url = row.get("booking_url") or ticket.booking_url
        ticket.fetched_at = now
    if kept:
        return f"The source no longer quotes {kept} of these flights; the earlier fare is kept."
    return None


def _apply_hotels(plan: TripPlan, payload: dict[str, Any], now: datetime) -> str | None:
    rows = {_name(row.get("name", "")): row for row in payload.get("hotels", [])}
    kept = 0
    for hotel in plan.hotels:
        row = rows.get(_name(hotel.name))
        if row is None or (hotel.price_per_night is not None and not row.get("nightly_price")):
            kept += 1
            continue
        if row.get("nightly_price"):
            hotel.price_per_night = Money.model_validate(row["nightly_price"])
            hotel.price_kind = PriceKind(row.get("price_kind", "none"))
        hotel.rating = row.get("rating", hotel.rating)
        hotel.review_count = row.get("review_count", hotel.review_count)
        hotel.source_refs = [SourceRef.model_validate(ref) for ref in row.get("source_refs", [])] or hotel.source_refs
        hotel.booking_url = row.get("booking_url") or hotel.booking_url
        hotel.fetched_at = now
    if kept:
        return f"The source no longer lists a price for {kept} of these stays; the earlier price is kept."
    return None


def _prices(plan: TripPlan) -> list[Any]:
    return [hotel.price_per_night for hotel in plan.hotels] + [ticket.price for ticket in plan.tickets]


def _name(value: str) -> str:
    return " ".join(value.casefold().split())


def _aware(stamp: datetime) -> datetime:
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
