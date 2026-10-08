"""Planner: merges agent outputs (+ similar saved trips) into a validated TripPlan.

Pipeline (proposal §4): the planner model compiles a TripPlan JSON -> the parser checks it
against the schema with bounded repair (default 2) -> if it still fails, `PlanInvalidError`
(the console shows an error and asks the user to retry or add detail; an invalid plan is never
returned). A valid draft is then grounded in code: every place, hotel, ticket and forecast must
resolve to a record an agent fetched in this turn, and sections whose source was unavailable are
left empty. Route order, time slots, cost and checks are computed deterministically.
"""

from __future__ import annotations

from collections.abc import Mapping

from backend.agents.checks import check_plan
from backend.agents.merge import (
    choose_hotel,
    choose_tickets,
    enforce_day_rules,
    fill_day,
    link_reservations,
    resolve_disruptions,
)
from backend.agents.plan_ops import (
    TARGET_ITEMS_PER_DAY,
    build_sections,
    compute_cost,
    order_and_schedule_day,
)
from backend.agents.prompts import PLANNER_INSTRUCTION, PLANNER_SYSTEM, Purpose, data_message, system
from backend.agents.route_order import NearestNeighbourOrderer, RouteOrderer
from backend.agents.runtime.caller import OutputRejected
from backend.agents.runtime.deps import RuntimeDeps
from backend.agents.runtime.structured import StructuredOutputError, complete_structured
from backend.schemas.agents import (
    AgentResult,
    AttractionData,
    HotelData,
    PlannerInput,
    TicketData,
    WeatherData,
)
from backend.schemas.common import AgentName, PathName, SectionStatus
from backend.schemas.observability import TraceContext
from backend.schemas.tools import FieldIssue
from backend.schemas.trip_plan import DayPlan, HotelStay, ItineraryItem, Place, TripPlan


class PlanInvalidError(Exception):
    """The TripPlan still failed the schema after bounded repair."""

    def __init__(self, message: str, issues: list[FieldIssue], attempts: int) -> None:
        super().__init__(message)
        self.issues = issues
        self.attempts = attempts


def _ok(results: Mapping[AgentName, AgentResult], agent: AgentName) -> AgentResult | None:
    result = results.get(agent)
    return result if result is not None and result.status is SectionStatus.OK else None


class Planner:
    def __init__(self, deps: RuntimeDeps, orderer: RouteOrderer | None = None) -> None:
        self.deps = deps
        self.orderer = orderer or NearestNeighbourOrderer()

    async def plan(
        self, planner_input: PlannerInput, *, trace: TraceContext, path: PathName = PathName.PLAN
    ) -> TripPlan:
        messages = [
            system(PLANNER_SYSTEM, self.deps.canary),
            data_message(PLANNER_INSTRUCTION, [("planner_input", planner_input.model_dump_json())]),
        ]
        try:
            draft = await complete_structured(
                self.deps.agent_caller,
                purpose=Purpose.PLANNER,
                messages=messages,
                schema=TripPlan,
                trace=trace,
                path=path,
                max_repairs=self.deps.settings.max_model_repairs,
                max_tokens=8192,
            )
        except StructuredOutputError as exc:
            raise PlanInvalidError(str(exc), exc.issues, exc.attempts) from exc
        except OutputRejected as exc:
            raise PlanInvalidError(f"planner output rejected: {exc.reason}", [], 1) from exc
        return self.finalise(draft, planner_input, trace=trace)

    def finalise(self, draft: TripPlan, pin: PlannerInput, *, trace: TraceContext) -> TripPlan:
        results = {r.agent: r for r in pin.results}
        ctx = pin.context
        attraction = _ok(results, AgentName.ATTRACTION)
        hotel_result = _ok(results, AgentName.HOTEL)
        weather = _ok(results, AgentName.WEATHER)
        tickets_result = _ok(results, AgentName.TICKET)
        ungrounded = 0

        # ---- places: only records the attraction agent fetched -----------------------------
        pool: dict[str, Place] = {}
        if attraction and isinstance(attraction.data, AttractionData):
            pool = {p.place_id: p for p in attraction.data.places}
        kept_places = [pool[p.place_id] for p in draft.places if p.place_id in pool]
        ungrounded += sum(1 for p in draft.places if p.place_id not in pool)

        # ---- forecasts: always from the weather agent, never from the draft ------------------
        forecasts = {}
        if weather and isinstance(weather.data, WeatherData):
            forecasts = {f.date: f for f in weather.data.forecasts}
        ungrounded += sum(
            1 for d in draft.days if d.forecast is not None and d.forecast.date not in forecasts
        )

        days: list[DayPlan] = []
        seen_items: set[str] = set()
        for day in draft.days:
            items: list[ItineraryItem] = []
            for item in day.items:
                if item.place_id not in pool:
                    ungrounded += 1
                    continue
                item_id = item.item_id if item.item_id not in seen_items else f"{item.place_id}@{day.date}"
                seen_items.add(item_id)
                place = pool[item.place_id]
                items.append(
                    item.model_copy(
                        update={
                            "item_id": item_id,
                            "title": place.name,
                            "confirmed": False,
                            "needs_reservation": place.needs_reservation,
                        }
                    )
                )
            days.append(DayPlan(date=day.date, items=items, forecast=forecasts.get(day.date)))

        referenced = {i.place_id for d in days for i in d.items}
        kept_ids = {p.place_id for p in kept_places}
        kept_places += [pool[pid] for pid in sorted(referenced - kept_ids)]

        # ---- hotel and tickets: chosen from fetched candidates only --------------------------
        hotel: HotelStay | None = None
        if hotel_result and isinstance(hotel_result.data, HotelData) and ctx.end_date and ctx.start_date:
            candidates = hotel_result.data.candidates
            wanted = draft.hotel.hotel.hotel_id if draft.hotel else None
            if wanted and wanted not in {h.hotel_id for h in candidates}:
                ungrounded += 1
                wanted = None
            hotel = choose_hotel(candidates, ctx, preferred_id=wanted)
        elif draft.hotel is not None:
            ungrounded += 1

        selected_tickets = []
        reservations = []
        if tickets_result and isinstance(tickets_result.data, TicketData):
            data = tickets_result.data
            known = {t.ticket_id for t in [*data.outbound, *data.inbound]}
            ungrounded += sum(1 for t in draft.tickets if t.ticket_id not in known)
            preferred = [t.ticket_id for t in draft.tickets if t.ticket_id in known]
            selected_tickets = choose_tickets(data, ctx, preferred_ids=preferred)
            reservations = data.reservations
        else:
            ungrounded += len(draft.tickets)

        plan = TripPlan(
            plan_id=pin.plan_id,
            version=(pin.previous_plan.version + 1) if pin.previous_plan else 1,
            destination=ctx.destination or draft.destination,
            origin=ctx.origin,
            start_date=draft.start_date,
            end_date=draft.end_date,
            party_size=ctx.party_size or draft.party_size,
            budget=ctx.budget,
            days=days,
            places=kept_places,
            hotel=hotel,
            tickets=selected_tickets,
            reservations=link_reservations(reservations, kept_places),
            sections=build_sections(results),
            saved_trip_refs=[s.trip_id for s in pin.saved_trips],
        )

        # ---- deterministic repairs of the schedule ------------------------------------------
        disruptions = [d for r in results.values() for d in r.disruptions]
        plan, rule_disruptions = enforce_day_rules(plan)
        disruptions += rule_disruptions
        new_days = []
        for day in plan.days:
            filled = fill_day(plan, day, list(pool.values()), target=TARGET_ITEMS_PER_DAY)
            ordered, _dropped = order_and_schedule_day(plan, filled, self.orderer)
            new_days.append(ordered)
        plan = plan.model_copy(update={"days": new_days})
        referenced = {i.place_id for d in plan.days for i in d.items}
        missing = [pool[pid] for pid in sorted(referenced - {p.place_id for p in plan.places})]
        plan = plan.model_copy(update={"places": [*plan.places, *missing]})
        plan = plan.model_copy(update={"disruptions": resolve_disruptions(plan, disruptions)})
        plan = plan.model_copy(update={"cost": compute_cost(plan)})
        plan = plan.model_copy(update={"violations": check_plan(plan, ctx)})

        if ungrounded:
            self.deps.observability.record_event(
                trace, "planner_ungrounded_removed", {"count": ungrounded}
            )
        return TripPlan.model_validate(plan.model_dump())
