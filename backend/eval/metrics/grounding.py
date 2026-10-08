"""Metric 3 - hallucination and grounding.

Appendix C: "generation joined to tool observations; code against the tool log. Each venue, hour,
price, and transit leg resolves to a fetched record in the same trace. Ungrounded entity rate
<= 5% on the custom sets. Not applied to TravelPlanner."

Every fact in a TripPlan carries a SourceRef(call_id). An entity is GROUNDED only if that call_id
names a ToolCallRecord with status OK in one of the session's own traces AND that record's
payload contains the entity with the same value (id or name for venues/hotels, the exact opening
hours, the exact price, the exact ticket leg, the exact forecast). A source that exists but says
something else is ungrounded, not grounded.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Sequence
from enum import StrEnum

from pydantic import BaseModel, Field

from backend.eval.harness import build_rig, eval_settings, inject_disruption
from backend.eval.models import CustomScenario, HKScenario, JapanScenario, scenario_message
from backend.eval.report import MetricReport, SummaryRow, fmt_rate, names_match
from backend.eval.stats import meets, rate
from backend.schemas.common import Money
from backend.schemas.observability import ToolCallRecord
from backend.schemas.tools import (
    ForecastPayload,
    PlaceRecord,
    PlacesPayload,
    ReservationsPayload,
    SearchPayload,
    TicketsPayload,
    ToolOutcomeStatus,
    ToolPayload,
)
from backend.schemas.trip_plan import DailyForecast, Hotel, Place, TicketOption, TripPlan
from backend.settings import Settings
from backend.tools.providers.mock.faults import FaultPlan

UNGROUNDED_TARGET = 0.05


class EntityKind(StrEnum):
    VENUE = "venue"
    HOURS = "hours"
    PRICE = "price"
    HOTEL = "hotel"
    TRANSIT_LEG = "transit_leg"
    FORECAST = "forecast"
    RESERVATION = "reservation"


class PlanEntity(BaseModel):
    kind: EntityKind
    ref: str = Field(description="Plan id of the entity (place_id, hotel_id, ticket_id, date)")
    label: str
    value: str = ""
    call_id: str


class GroundingCheck(BaseModel):
    entity: PlanEntity
    grounded: bool
    reason: str


_Predicate = Callable[[ToolPayload], bool]


def _money_eq(a: Money | None, b: Money | None) -> bool:
    return a is not None and b is not None and a.currency == b.currency and abs(a.amount - b.amount) < 0.01


def _place_records(payload: ToolPayload, ref: str, name: str) -> list[PlaceRecord]:
    if not isinstance(payload, PlacesPayload):
        return []
    return [r for r in payload.places if r.place_id == ref or names_match(r.name, name)]


def _search_snippets(payload: ToolPayload, name: str) -> list[str]:
    if not isinstance(payload, SearchPayload):
        return []
    return [h.snippet for h in payload.hits if names_match(h.title, name)]


def _place_entities(place: Place) -> list[tuple[PlanEntity, _Predicate]]:
    call = place.source.call_id
    ref, name = place.place_id, place.name
    out: list[tuple[PlanEntity, _Predicate]] = [
        (
            PlanEntity(kind=EntityKind.VENUE, ref=ref, label=name, call_id=call),
            lambda p: bool(_place_records(p, ref, name) or _search_snippets(p, name)),
        )
    ]
    if place.opening_hours:
        hours = place.opening_hours
        out.append(
            (
                PlanEntity(kind=EntityKind.HOURS, ref=ref, label=name, value=hours, call_id=call),
                lambda p: any(r.opening_hours == hours for r in _place_records(p, ref, name)),
            )
        )
    if place.price is not None:
        price = place.price

        def price_ok(p: ToolPayload) -> bool:
            if any(_money_eq(r.price, price) for r in _place_records(p, ref, name)):
                return True
            text = f"{price.amount:,.0f} {price.currency}"
            return any(
                text in s or (price.amount == 0 and "free" in s.casefold()) for s in _search_snippets(p, name)
            )

        out.append(
            (
                PlanEntity(
                    kind=EntityKind.PRICE, ref=ref, label=name, value=f"{price.amount:g} {price.currency}", call_id=call
                ),
                price_ok,
            )
        )
    return out


def _hotel_entities(hotel: Hotel) -> list[tuple[PlanEntity, _Predicate]]:
    call, ref, name, price = hotel.source.call_id, hotel.hotel_id, hotel.name, hotel.nightly_price
    return [
        (
            PlanEntity(kind=EntityKind.HOTEL, ref=ref, label=name, call_id=call),
            lambda p: bool(_place_records(p, ref, name)),
        ),
        (
            PlanEntity(
                kind=EntityKind.PRICE, ref=ref, label=name, value=f"{price.amount:g} {price.currency}", call_id=call
            ),
            lambda p: any(_money_eq(r.price, price) for r in _place_records(p, ref, name)),
        ),
    ]


def _ticket_entities(ticket: TicketOption) -> list[tuple[PlanEntity, _Predicate]]:
    call, ref = ticket.source.call_id, ticket.ticket_id

    def leg_ok(p: ToolPayload) -> bool:
        if not isinstance(p, TicketsPayload):
            return False
        return any(
            r.ticket_id == ref
            and r.depart_at == ticket.depart_at
            and r.arrive_at == ticket.arrive_at
            and r.mode == ticket.mode
            and r.status == ticket.status
            and r.delay_minutes == ticket.delay_minutes
            for r in p.tickets
        )

    def price_ok(p: ToolPayload) -> bool:
        return isinstance(p, TicketsPayload) and any(
            r.ticket_id == ref and _money_eq(r.price, ticket.price) for r in p.tickets
        )

    label = f"{ticket.direction} {ticket.mode} {ticket.origin}->{ticket.destination} {ticket.depart_at:%Y-%m-%d %H:%M}"
    return [
        (PlanEntity(kind=EntityKind.TRANSIT_LEG, ref=ref, label=label, value=ticket.status, call_id=call), leg_ok),
        (
            PlanEntity(
                kind=EntityKind.PRICE, ref=ref, label=label, value=f"{ticket.price.amount:g} {ticket.price.currency}",
                call_id=call,
            ),
            price_ok,
        ),
    ]


def _forecast_entity(forecast: DailyForecast) -> tuple[PlanEntity, _Predicate]:
    def ok(p: ToolPayload) -> bool:
        return isinstance(p, ForecastPayload) and any(
            d.date == forecast.date
            and d.summary == forecast.summary
            and d.temp_min_c == forecast.temp_min_c
            and d.temp_max_c == forecast.temp_max_c
            and d.warning_signal == forecast.warning_signal
            for d in p.days
        )

    value = f"{forecast.summary}; signal={forecast.warning_signal}"
    entity = PlanEntity(
        kind=EntityKind.FORECAST, ref=forecast.date.isoformat(), label=forecast.date.isoformat(), value=value,
        call_id=forecast.source.call_id,
    )
    return entity, ok


def _entity_checks(plan: TripPlan) -> list[tuple[PlanEntity, _Predicate]]:
    out: list[tuple[PlanEntity, _Predicate]] = []
    for place in plan.places:
        out += _place_entities(place)
    if plan.hotel is not None:
        out += _hotel_entities(plan.hotel.hotel)
    for ticket in plan.tickets:
        out += _ticket_entities(ticket)
    for day in plan.days:
        if day.forecast is not None:
            out.append(_forecast_entity(day.forecast))
    for res in plan.reservations:
        name, lead = res.place_name, res.lead_time_days
        out.append(
            (
                PlanEntity(
                    kind=EntityKind.RESERVATION, ref=res.place_id or name, label=name, value=f"{lead} days",
                    call_id=res.source.call_id,
                ),
                lambda p, name=name, lead=lead: isinstance(p, ReservationsPayload)
                and any(names_match(r.place_name, name) and r.lead_time_days == lead for r in p.reservations),
            )
        )
    return out


def extract_entities(plan: TripPlan) -> list[PlanEntity]:
    """Every checkable fact of the final plan: venues, hours, prices, hotel, ticket legs,
    forecasts, reservations."""
    return [entity for entity, _ in _entity_checks(plan)]


def ground_plan(plan: TripPlan, records: Sequence[ToolCallRecord]) -> list[GroundingCheck]:
    """Join each entity's SourceRef.call_id to the tool log of the same session (pure)."""
    by_call: dict[str, ToolCallRecord] = {}
    for r in records:
        by_call.setdefault(r.call_id, r)
    out: list[GroundingCheck] = []
    for entity, predicate in _entity_checks(plan):
        record = by_call.get(entity.call_id)
        if record is None:
            out.append(GroundingCheck(entity=entity, grounded=False, reason="call_id not in this session's traces"))
        elif record.status is not ToolOutcomeStatus.OK or record.payload is None:
            out.append(GroundingCheck(entity=entity, grounded=False, reason=f"source call status {record.status.value}"))
        elif not predicate(record.payload):
            out.append(GroundingCheck(entity=entity, grounded=False, reason="value not in the fetched record"))
        else:
            out.append(GroundingCheck(entity=entity, grounded=True, reason="ok"))
    return out


class SessionGrounding(BaseModel):
    scenario_id: str
    entities: int
    ungrounded: int
    plan_produced: bool
    failures: list[GroundingCheck] = Field(default_factory=list, description="Ungrounded entities only")


class KindCount(BaseModel):
    entities: int = 0
    ungrounded: int = 0
    rate: float | None = None


class GroundingScores(BaseModel):
    sessions: int
    entities: int
    ungrounded: int
    ungrounded_rate: float | None
    target: float = UNGROUNDED_TARGET
    meets_target: bool | None
    per_kind: dict[str, KindCount]


def score_session(scenario_id: str, plan: TripPlan | None, records: Sequence[ToolCallRecord]) -> SessionGrounding:
    if plan is None:
        return SessionGrounding(scenario_id=scenario_id, entities=0, ungrounded=0, plan_produced=False)
    checks = ground_plan(plan, records)
    failures = [c for c in checks if not c.grounded]
    return SessionGrounding(
        scenario_id=scenario_id, entities=len(checks), ungrounded=len(failures), plan_produced=True, failures=failures
    )


def score_grounding(sessions: Sequence[SessionGrounding], checks: Sequence[GroundingCheck] = ()) -> GroundingScores:
    entities = sum(s.entities for s in sessions)
    ungrounded = sum(s.ungrounded for s in sessions)
    totals: Counter[str] = Counter(c.entity.kind.value for c in checks)
    bad: Counter[str] = Counter(c.entity.kind.value for c in checks if not c.grounded)
    per_kind = {k: KindCount(entities=n, ungrounded=bad[k], rate=rate(bad[k], n)) for k, n in sorted(totals.items())}
    value = rate(ungrounded, entities)
    return GroundingScores(
        sessions=len(sessions),
        entities=entities,
        ungrounded=ungrounded,
        ungrounded_rate=value,
        meets_target=meets(value, UNGROUNDED_TARGET, higher_is_better=False),
        per_kind=per_kind,
    )


class GroundingReport(MetricReport):
    metric: str = "grounding"
    scores: GroundingScores
    items: list[SessionGrounding] = Field(default_factory=list)

    def summary_row(self) -> SummaryRow:
        s = self.scores
        return SummaryRow(
            metric=self.metric,
            split=self.split,
            n=s.sessions,
            headline=f"ungrounded {s.ungrounded}/{s.entities} = {fmt_rate(s.ungrounded_rate)}",
            target="<= 5.0%",
            met=s.meets_target,
        )


async def run_grounding(
    scenarios: Sequence[CustomScenario],
    *,
    split_name: str = "all",
    settings: Settings | None = None,
) -> GroundingReport:
    """Run each custom scenario's full session (Japan: plan + edit; Hong Kong: plan +
    disruption + follow-up) and ground the FINAL plan against every trace of that session."""
    items: list[SessionGrounding] = []
    all_checks: list[GroundingCheck] = []
    for scenario in scenarios:
        faults = FaultPlan()
        async with build_rig(settings or eval_settings(), faults=faults) as rig:
            session = await rig.new_session(scenario.context)
            result = await rig.turn(session, scenario_message(scenario))
            if isinstance(scenario, JapanScenario) and scenario.edit is not None and result.plan is not None:
                result = await rig.turn(session, scenario.edit.message)
            elif isinstance(scenario, HKScenario) and result.plan is not None:
                inject_disruption(faults, scenario, result.plan)
                await rig.age_sections(session, scenario.age_hours)
                result = await rig.turn(session, scenario.followup)
            plan = (await rig.container.orchestrator.get_session(session)).plan
            records = rig.session_tool_log(session)
            items.append(score_session(scenario.id, plan, records))
            if plan is not None:
                all_checks += ground_plan(plan, records)
    return GroundingReport(split=split_name, scores=score_grounding(items, all_checks), items=items)
