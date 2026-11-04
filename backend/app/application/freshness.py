"""Outdated forecasts, fares and hotel prices are re-fetched by their own agent (Proposal DB-13).

Time-sensitive sections carry the time they were fetched. On a later turn, and when
a saved plan is reopened, each section's oldest stamp is compared with its window
(``Settings.weather_fresh_seconds``, ``ticket_fresh_seconds``, ``hotel_fresh_seconds``).
An outdated section is marked on the plan (``data_quality.freshness``) and fetched
again by the agent that owns it, through the Toolbox ``refresh`` path (allowlisted and
logged). Every other section of the plan is left as it was.

A section whose source failed when the plan was made has no fetch time to compare. When the plan
holds none of it and names a retryable gap for it, it is asked for again the same way: when the plan
is reopened or a turn reads it (while its source stays down, no more often than every
``RETRY_AFTER_SECONDS``) and whenever the traveller asks to refresh. An outdated section that its source
could not renew is kept to the same record: it stays flagged as outdated, and is asked again no sooner.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from app.application.agents import AgentContext, BaseAgent, HotelAgent, TicketAgent, WeatherAgent
from app.application.planner import Planner, _constraints, _reservations
from app.application.toolbox import Toolbox
from app.config import Settings
from app.infrastructure.store import PlanConflict
from app.domain.models import (
    AgentResult,
    ConstraintCheck,
    DataStatus,
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
from app.infrastructure.observability import NoopTracer, start_trace

logger = logging.getLogger(__name__)

SECTIONS = ("weather", "tickets", "hotels")
LABELS = {"weather": "weather forecast", "tickets": "flight fares", "hotels": "hotel prices"}

#: A section that never arrived, or that its source could not renew, is asked for again at most this often
#: (seconds) unless the traveller asks.
RETRY_AFTER_SECONDS = 600
NOT_ASKED_YET = (
    f"The source was asked less than {RETRY_AFTER_SECONDS // 60} minutes ago and had nothing newer; "
    "the earlier data is kept."
)


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


def missing_sections(plan: TripPlan) -> list[str]:
    """The time-sensitive sections whose data never arrived and may on another try.

    ``section_stamps`` sees only data that exists, so a section whose source failed when the plan was
    made would never be outdated. It is missing when the plan holds none of it (a forecast counts only
    when it covers every day, and a seasonal reference that stood in for a source that failed is not one)
    and names a retryable gap for it: a source that answered "no data" is not asked again.
    """
    holds = {
        "weather": all(day.weather is not None and day.weather.status == DataStatus.OK for day in plan.days),
        "tickets": any(ticket.mode == "flight" for ticket in plan.tickets),
        "hotels": bool(plan.hotels),
    }
    retryable = {gap.section for gap in plan.data_quality.gaps if gap.retryable}
    return [section for section in SECTIONS if section in retryable and not holds[section]]


@dataclass
class RefreshOutcome:
    plan: TripPlan
    #: The sections older than their window.
    outdated: list[str]
    #: The sections that came back with data: outdated ones fetched again and missing ones fetched at last.
    refreshed: list[str]
    #: The sections the plan still holds none of after this check.
    missing: list[str] = field(default_factory=list)


def _never_busy(_: str) -> bool:
    return False


class FreshnessService:
    def __init__(
        self,
        *,
        registry: Any,
        planner: Any,
        settings: Settings,
        store: Any | None = None,
        clock: Callable[[], datetime] = utcnow,
        session_busy: Callable[[str], bool] = _never_busy,
    ):
        self.registry = registry
        self.planner = planner
        self.settings = settings
        self.store = store
        self.clock = clock
        #: Whether a turn is running on a trip (the turn service sets this): a reopened plan is
        #: not saved over a turn's work.
        self.session_busy = session_busy
        #: When a missing section of a trip was last asked for again, by (trip id, section): a source
        #: that is down is asked again after ``RETRY_AFTER_SECONDS``, not on every reopen.
        self._tried: dict[tuple[str, str], datetime] = {}

    def _missing_due(self, plan: TripPlan, now: datetime, *, requested: bool) -> list[str]:
        """The missing sections to ask for again now: all of them when the traveller asked to refresh,
        otherwise those not asked within ``RETRY_AFTER_SECONDS`` (the plan's own time stands for the
        first try, made when it was built). Nothing about a trip that has ended is asked for again."""
        if plan.dates.end < now.date():
            return []
        never = datetime.min.replace(tzinfo=timezone.utc)
        due: list[str] = []
        for section in missing_sections(plan):
            tried = max(_aware(plan.updated_at), self._tried.get((plan.trip_id, section), never))
            if requested or (now - tried).total_seconds() > RETRY_AFTER_SECONDS:
                due.append(section)
        return due

    def _to_fetch(
        self, plan: TripPlan, outdated: dict[str, datetime], now: datetime, *, requested: bool
    ) -> tuple[list[str], list[str]]:
        """The outdated sections to ask for again now, and the missing ones to ask for. An outdated section
        that was asked and could not be renewed waits ``RETRY_AFTER_SECONDS`` (it is flagged meanwhile), unless
        the traveller asked to refresh."""
        never = datetime.min.replace(tzinfo=timezone.utc)
        renew = [
            section
            for section in outdated
            if requested or (now - self._tried.get((plan.trip_id, section), never)).total_seconds() > RETRY_AFTER_SECONDS
        ]
        absent = [section for section in self._missing_due(plan, now, requested=requested) if section not in outdated]
        return renew, absent

    def needs_check(self, plan: TripPlan, *, requested: bool = False) -> bool:
        now = _aware(self.clock())
        outdated = outdated_sections(plan, self.settings, now)
        cleared = any(
            mark.status == "outdated" and mark.section not in outdated for mark in plan.data_quality.freshness
        )
        return bool(outdated) or cleared or bool(self._missing_due(plan, now, requested=requested))

    async def check(
        self,
        plan: TripPlan,
        *,
        context: TripContext | None,
        tracer: Tracer,
        request_id: str,
        save: bool = True,
        requested: bool = False,
    ) -> RefreshOutcome | None:
        """Mark and re-fetch outdated sections, and fetch again a section that never arrived.

        None when every section is in date and nothing is due. A plan in which a section was
        re-fetched is saved as the next version, unless the caller saves it (``save=False``).
        ``requested`` is the traveller asking to refresh: a missing section is then asked for again
        whenever it was last tried, and the outcome says what is still missing.
        """
        if not self.needs_check(plan, requested=requested):
            return None
        now = _aware(self.clock())
        outdated = outdated_sections(plan, self.settings, now)
        renew, absent = self._to_fetch(plan, outdated, now, requested=requested)
        stale_marks = any(
            mark.status == "outdated" and mark.section not in outdated for mark in plan.data_quality.freshness
        )
        for section in [*renew, *absent]:
            # Noted before the fetch, so a second request arriving meanwhile does not ask again.
            self._tried[(plan.trip_id, section)] = now
        self._tried = {
            key: at for key, at in self._tried.items() if (now - at).total_seconds() <= RETRY_AFTER_SECONDS
        }
        updated = plan.model_copy(deep=True)
        toolbox = Toolbox(
            path="refresh",
            tracer=tracer,
            store=self.store,
            request_id=request_id,
            max_calls=self.settings.max_tool_calls_per_turn or None,
        )
        request_context = self._agent_context(updated, context)
        with tracer.span("freshness", outdated=sorted(outdated), missing=sorted(absent)) as span:
            sections = [*renew, *absent]
            prices_before = _prices(updated)
            checks_before = _derived_checks(updated, request_context)
            try:
                answers = await asyncio.gather(
                    *(self._refetch(section, updated, request_context, toolbox, now) for section in sections)
                )
            except BaseException:
                # Cancelled (the reply was stopped) or failed: nothing was asked to the end, so another look may ask.
                for section in sections:
                    self._tried.pop((plan.trip_id, section), None)
                raise
            fetched = dict(zip(sections, answers))
            marks = {
                mark.section: mark
                for mark in updated.data_quality.freshness
                if not (mark.status == "outdated" and mark.section not in outdated)
            }
            refreshed = [section for section, (note, _) in fetched.items() if note is None]
            for section in refreshed:
                self._tried.pop((plan.trip_id, section), None)  # renewed or filled: nothing to wait for
            # A section asked and not renewed keeps the record made before the fetch: not asked again for a while.
            for section in outdated:
                note = fetched[section][0] if section in fetched else NOT_ASKED_YET
                marks[section] = FreshnessMark(
                    section=section,  # type: ignore[arg-type]
                    status="refreshed" if note is None else "outdated",
                    fetched_at=outdated[section],
                    checked_at=now,
                    window_seconds=window_seconds(section, self.settings),
                    note=note or "",
                )
            updated.data_quality.freshness = [marks[section] for section in SECTIONS if section in marks]
            filled = [section for section in absent if section in refreshed]
            for section in filled:
                result = fetched[section][1]
                if result is not None:
                    self._settle_filled(updated, section, result)
            priced_again = _prices(updated) != prices_before
            if priced_again:
                updated.cost = self.planner._cost(
                    request_context,
                    updated.hotels[0] if updated.hotels else None,
                    updated.tickets,
                    updated.dates,
                )
            if filled or priced_again:
                # The checks that read the plan's own data (the budget among them) follow the cost they read.
                updated.constraints = _recheck_derived(updated, request_context, checks_before)
            span.attrs["refreshed"] = refreshed
        if not (outdated or refreshed or requested or stale_marks):
            return None  # only a section that never arrived was tried again, and nothing came back
        if refreshed:
            renewed = [section for section in refreshed if section in outdated]
            said = []
            if renewed:
                said.append("Refreshed the outdated " + _join([LABELS[section] for section in renewed]) + ".")
            if filled:
                said.append("Fetched the missing " + _join([LABELS[section] for section in filled]) + ".")
            updated.version = plan.version + 1
            updated.updated_at = utcnow()
            updated.change_summary = " ".join(said)
            if save and self.store is not None:
                try:
                    self.store.save_plan(updated, updated.change_summary)
                except PlanConflict:
                    # Another request refreshed this plan first; its version stands, ours is shown.
                    logger.info("a concurrent request already saved version %s of this plan", updated.version)
        return RefreshOutcome(
            plan=updated, outdated=sorted(outdated), refreshed=refreshed, missing=missing_sections(updated)
        )

    @staticmethod
    def _settle_filled(plan: TripPlan, section: str, result: AgentResult) -> None:
        """A section that came back: its gaps are what this fetch could not get, and its sources are named."""
        quality = plan.data_quality
        quality.gaps = [gap for gap in quality.gaps if gap.section != section] + list(result.failures)
        quality.sources_used = sorted({*quality.sources_used, *(ref.source for ref in result.sources)})

    async def on_reopen(self, plan: TripPlan) -> TripPlan:
        """A saved plan opened again: the same check as a later turn, under its own request id.

        A turn on the trip has read the plan and will save the next version, so a refresh saved
        meanwhile would make it end in a conflict. While a turn runs the plan is shown as stored (the
        turn refreshes an outdated plan itself before it reads it); otherwise the refresh is saved
        only if the plan is still the one it was made from.
        """
        if plan is None or not self.needs_check(plan) or self.session_busy(plan.session_id):
            return plan
        request_id = new_id("req")
        now = _aware(self.clock())
        renew, absent = self._to_fetch(plan, outdated_sections(plan, self.settings, now), now, requested=False)
        # A look at the trip that asks no source has nothing to record, however often the page looks.
        tracer = start_trace(self.settings, request_id) if renew or absent else NoopTracer()
        context = self.store.get_context(plan.session_id) if self.store is not None else None
        try:
            outcome = await self.check(plan, context=context, tracer=tracer, request_id=request_id, save=False)
        except Exception:  # a failed refresh never stops the plan from opening
            logger.exception("refreshing an outdated plan failed")
            tracer.finish(status="error")
            return plan
        tracer.finish(status="ok")
        return plan if outcome is None else self._save_reopened(plan, outcome)

    def _save_reopened(self, base: TripPlan, outcome: RefreshOutcome) -> TripPlan:
        """Save a refresh made for ``base`` and return the plan to show: the stored one if it moved on.

        That holds when nothing was refreshed too: the marks of a failed refresh belong on the version the
        traveller has, not on an older one next to a newer version in the list."""
        if self.store is None:
            return outcome.plan
        stored = self.store.get_plan(base.session_id)
        moved_on = stored is None or (stored.trip_id, stored.version) != (base.trip_id, base.version)
        if moved_on:
            return stored or base  # a newer version took the trip over while this was fetched
        if not outcome.refreshed:
            return outcome.plan  # nothing to save: the stored plan, with the sections it could not refresh flagged
        if self.session_busy(base.session_id):
            return stored or base  # a turn is running on the trip and will save the next version
        try:
            self.store.save_plan(outcome.plan, outcome.plan.change_summary)
        except PlanConflict:
            return self.store.get_plan(base.session_id) or base
        return outcome.plan

    def _agent_context(self, plan: TripPlan, context: TripContext | None) -> TripContext:
        """The trip as the plan states it: its destination, dates, origin, party size, budget and currency.

        The stored form may say something else by now (a Save that did not re-plan, a question about another
        city), and a source is asked about the trip that was planned, whose cost is worked out in its currency."""
        base = context or TripContext(session_id=plan.session_id)
        return base.model_copy(
            update={
                "destination": plan.destination.name,
                "origin": plan.origin or base.origin,
                "start_date": plan.dates.start,
                "end_date": plan.dates.end,
                "days": len(plan.dates.dates),
                "party_size": plan.party_size,
                "budget": plan.budget.amount if plan.budget else None,
                "currency": plan.cost.currency or self.settings.default_currency,
            }
        )

    async def _refetch(
        self,
        section: str,
        plan: TripPlan,
        context: TripContext,
        toolbox: Toolbox,
        now: datetime,
    ) -> tuple[str | None, AgentResult | None]:
        """Re-run the section's own agent and apply what it returned. The note is None when refreshed,
        otherwise why the earlier data was kept (or why nothing came); the agent's result comes with it
        when the agent ran."""
        if plan.destination.geo is None:
            return "The destination has no coordinates, so the source could not be asked again.", None
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
            return "The source could not be reached; the earlier data is kept.", None
        apply = {"weather": _apply_weather, "tickets": _apply_fares, "hotels": _apply_hotels}[section]
        return apply(plan, result.payload, now), result


def _apply_weather(plan: TripPlan, payload: dict[str, Any], now: datetime) -> str | None:
    rows = {row.get("date"): row for row in payload.get("days", []) if row.get("date")}
    held = any(day.weather is not None for day in plan.days)
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
    if not held and all(day.weather is None for day in plan.days):
        return "No forecast came back for these dates."
    return None


def _apply_fares(plan: TripPlan, payload: dict[str, Any], now: datetime) -> str | None:
    rows: dict[str, dict[str, Any]] = {}
    for row in payload.get("flights", []):
        name = " ".join(part for part in [row.get("airline"), row.get("flight_number")] if part) or "Flight"
        rows.setdefault(name.casefold(), row)
    if not any(ticket.mode == "flight" for ticket in plan.tickets):
        # The fares never arrived: the options a new plan builds from what came back.
        flights = Planner._build_tickets(list(payload.get("flights", [])))
        if not flights:
            return "No flight came back for these dates."
        for ticket in flights:
            ticket.fetched_at = now
        plan.tickets = [*plan.tickets, *flights]
        plan.reservations = [*plan.reservations, *_reservations([], flights)]
        return None
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
    if not plan.hotels:
        # The stay never arrived: the pick a new plan makes from what came back (its first candidate).
        stay = Planner._build_hotel(Planner._choose_hotel(list(payload.get("hotels", [])), None), plan.dates)
        if stay is None:
            return "No hotel came back for these dates."
        stay.fetched_at = now
        plan.hotels = [stay]
        return None
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


def _derived_checks(plan: TripPlan, context: TripContext) -> dict[str, ConstraintCheck]:
    """The checks the planner reads off the plan's own data (budget, forecast, stay, fares), by text.
    The checks that come from the traveller's own rules need what only the planner knew and are left out."""
    bare = context.model_copy(update={"hard_constraints": [], "preferences": []})
    checks = _constraints(bare, plan.days, plan.hotels[0] if plan.hotels else None, plan.tickets, plan.cost)
    return {check.text: check for check in checks}


def _recheck_derived(
    plan: TripPlan, context: TripContext, before: dict[str, ConstraintCheck]
) -> list[ConstraintCheck]:
    """The plan's checks, with those that a section just fetched changed read again; the others stay as they were."""
    after = _derived_checks(plan, context)
    changed = {
        text: check
        for text, check in after.items()
        if text not in before or (check.status, check.evidence) != (before[text].status, before[text].evidence)
    }
    return [
        check.model_copy(update={"status": changed[check.text].status, "evidence": changed[check.text].evidence})
        if check.text in changed
        else check
        for check in plan.constraints
    ]


def _name(value: str) -> str:
    return " ".join(value.casefold().split())


def _aware(stamp: datetime) -> datetime:
    return stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=timezone.utc)


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
