"""Quick-question path.

Proposal: "A quick question is one model call, with a tool call only if the plan does not already
hold the answer" and "it checks whether the question is related to the existing plan". The model
call decides what data the answer needs; code then checks the current TripPlan first (fresh data
only) and makes at most ONE tool call otherwise. Answers built from data are rendered by code from
the plan or the tool result, so they always cite a fetched source and never come from model
knowledge. No pre-planning agent runs on this path.
"""

from __future__ import annotations

import json
from datetime import date

from pydantic import JsonValue, TypeAdapter, ValidationError

from backend.agents.geo import normalise_name
from backend.agents.preplanning.base import source_of
from backend.agents.focus import focus_summary
from backend.agents.prompts import (
    ASK_FOCUS_INSTRUCTION,
    ASK_INSTRUCTION,
    ASK_SYSTEM,
    Purpose,
    data_message,
    system,
)
from backend.agents.runtime.caller import ModelCallError, OutputRejected
from backend.agents.runtime.deps import RuntimeDeps
from backend.agents.runtime.structured import StructuredOutputError, complete_structured
from backend.schemas.common import AgentName, PathName, StrictModel
from backend.schemas.memory import SessionState
from backend.schemas.routing import PlanFocus
from backend.schemas.observability import TraceContext
from backend.schemas.tools import (
    ForecastDay,
    ForecastPayload,
    ForecastRequest,
    GeocodePayload,
    GeocodeRequest,
    PlacesPayload,
    PlacesSearchRequest,
    ReservationCheckRequest,
    TicketRecord,
    TicketSearchRequest,
    TicketsPayload,
    ToolOutcome,
    ToolRequest,
    WebSearchRequest,
)
from backend.schemas.trip_plan import SourceRef, TripPlan
from backend.schemas.turn import QuickAnswer
from backend.tools.staleness import is_stale

_REQUEST = TypeAdapter(ToolRequest)


class AskDecision(StrictModel):
    related_to_plan: bool
    answer: str | None = None
    tool_call: dict[str, JsonValue] | None = None


def plan_facts(plan: TripPlan | None) -> str:
    """Compact facts the model may answer from (the same data code checks)."""
    if plan is None:
        return "null"
    facts = {
        "destination": plan.destination,
        "origin": plan.origin,
        "start_date": plan.start_date.isoformat(),
        "end_date": plan.end_date.isoformat(),
        "party_size": plan.party_size,
        "hotel": plan.hotel.hotel.name if plan.hotel else None,
        "days": [
            {
                "date": d.date.isoformat(),
                "stops": [i.title for i in d.items],
                "forecast": d.forecast.summary if d.forecast else None,
            }
            for d in plan.days
        ],
        "tickets": [
            f"{t.direction}: {t.carrier} {t.origin}->{t.destination} {t.depart_at:%Y-%m-%d %H:%M}"
            for t in plan.tickets
        ],
    }
    return json.dumps(facts, ensure_ascii=False)


def _city_matches(a: str, b: str) -> bool:
    x, y = normalise_name(a), normalise_name(b)
    return bool(x and y) and (x in y or y in x)


def _request_place(request: ToolRequest) -> str | None:
    if isinstance(request, ForecastRequest):
        return request.location
    if isinstance(request, (PlacesSearchRequest, WebSearchRequest, ReservationCheckRequest)):
        return request.destination
    if isinstance(request, TicketSearchRequest):
        return request.destination
    if isinstance(request, GeocodeRequest):
        return request.city
    return None


def _request_dates(request: ToolRequest) -> list[date]:
    if isinstance(request, ForecastRequest):
        return [request.start_date, request.end_date]
    if isinstance(request, TicketSearchRequest):
        return [request.travel_date]
    return []


def is_related(plan: TripPlan | None, request: ToolRequest | None, model_says: bool) -> bool:
    if plan is None:
        return False
    if request is None:
        return model_says
    place = _request_place(request)
    in_place = place is None or _city_matches(place, plan.destination) or (
        plan.origin is not None and _city_matches(place, plan.origin)
    )
    in_dates = all(plan.start_date <= d <= plan.end_date for d in _request_dates(request))
    return in_place and in_dates


def _fmt_forecast(location: str, day: ForecastDay, source: SourceRef) -> str:
    warning = f" Warning: {day.warning_signal}." if day.warning_signal else ""
    return (
        f"{location} on {day.date:%d %b %Y}: {day.summary}, {day.temp_min_c:.0f}-{day.temp_max_c:.0f} C, "
        f"{day.precipitation_chance:.0%} chance of rain.{warning} "
        f"(source: {source.provider}, fetched {source.fetched_at:%Y-%m-%d %H:%M} UTC)"
    )


def _fmt_ticket(t: TicketRecord) -> str:
    status = "" if t.status == "scheduled" else f" [{t.status}{f' +{t.delay_minutes} min' if t.delay_minutes else ''}]"
    return (
        f"{t.carrier} {t.origin} {t.depart_at:%H:%M} -> {t.destination} {t.arrive_at:%H:%M}, "
        f"{t.price.amount:.0f} {t.price.currency}{status}"
    )


def answer_from_plan(
    request: ToolRequest, plan: TripPlan, deps: RuntimeDeps
) -> tuple[str, list[SourceRef]] | None:
    """Return an answer if the current plan already holds the (fresh) fact."""
    if isinstance(request, ForecastRequest):
        if not _city_matches(request.location, plan.destination):
            return None
        section = plan.section(AgentName.WEATHER)
        if section is None or is_stale(section.fetched_at, AgentName.WEATHER, deps.settings):
            return None
        wanted = [d for d in plan.days if request.start_date <= d.date <= request.end_date]
        if not wanted or any(d.forecast is None for d in wanted):
            return None
        lines = [
            _fmt_forecast(plan.destination, ForecastDay(**d.forecast.model_dump(exclude={"source"})), d.forecast.source)
            for d in wanted
            if d.forecast is not None
        ]
        return " ".join(lines), [d.forecast.source for d in wanted if d.forecast is not None]
    if isinstance(request, TicketSearchRequest):
        section = plan.section(AgentName.TICKET)
        if section is None or is_stale(section.fetched_at, AgentName.TICKET, deps.settings):
            return None
        matches = [
            t
            for t in plan.tickets
            if t.depart_at.date() == request.travel_date
            and _city_matches(t.origin, request.origin)
            and _city_matches(t.destination, request.destination)
        ]
        if not matches:
            return None
        text = "Your plan has: " + "; ".join(
            _fmt_ticket(TicketRecord(**t.model_dump(exclude={"direction", "confirmed", "source"})))
            for t in matches
        )
        return text, [t.source for t in matches]
    if isinstance(request, PlacesSearchRequest) and request.category == "hotel" and plan.hotel:
        h = plan.hotel.hotel
        return (
            f"Your hotel is {h.name} ({h.nightly_price.amount:.0f} {h.nightly_price.currency}/night"
            + (f", rating {h.rating}" if h.rating else "")
            + ").",
            [h.source],
        )
    if isinstance(request, GeocodeRequest):
        key = normalise_name(request.query)
        place = next((p for p in plan.places if normalise_name(p.name) == key and p.location), None)
        if place and place.location:
            return (
                f"{place.name} is at {place.location.lat:.5f}, {place.location.lng:.5f}"
                + (f" ({place.address})" if place.address else "")
                + ".",
                [place.geocode_source or place.source],
            )
    return None


def answer_from_tool(outcome: ToolOutcome, request: ToolRequest) -> str:
    source = source_of(outcome)
    payload = outcome.payload
    if isinstance(payload, ForecastPayload) and isinstance(request, ForecastRequest):
        return " ".join(_fmt_forecast(request.location, d, source) for d in payload.days) or "No forecast returned."
    if isinstance(payload, TicketsPayload):
        if not payload.tickets:
            return "No trains or flights were found for that date."
        return "Options: " + "; ".join(_fmt_ticket(t) for t in payload.tickets[:3]) + f" (source: {source.provider})"
    if isinstance(payload, PlacesPayload):
        names = ", ".join(p.name for p in payload.places[:5]) or "nothing found"
        return f"From {source.provider}: {names}."
    if isinstance(payload, GeocodePayload):
        r = payload.result
        return f"{r.formatted_address}: {r.location.lat:.5f}, {r.location.lng:.5f} (source: {source.provider})."
    return "The source returned data I cannot summarise."


class AskPath:
    def __init__(self, deps: RuntimeDeps) -> None:
        self.deps = deps

    async def run(
        self, state: SessionState, message: str, *, trace: TraceContext, focus: PlanFocus | None = None
    ) -> QuickAnswer:
        plan = state.plan
        blocks = [("plan_facts", plan_facts(plan)), ("trip_context", state.context.model_dump_json())]
        if focus is not None and plan is not None:
            blocks.append(("focus", focus_summary(plan, focus)))
        blocks.append(("question", message))
        instruction = ASK_FOCUS_INSTRUCTION if focus is not None else ASK_INSTRUCTION
        messages = [system(ASK_SYSTEM, self.deps.canary), data_message(instruction, blocks)]
        try:
            decision = await complete_structured(
                self.deps.agent_caller,
                purpose=Purpose.ASK,
                messages=messages,
                schema=AskDecision,
                trace=trace,
                path=PathName.ASK,
                max_repairs=self.deps.settings.max_model_repairs,
            )
        except (ModelCallError, OutputRejected, StructuredOutputError):
            return QuickAnswer(
                text="Sorry, I could not answer that. Could you rephrase the question?",
                from_plan=False,
                related_to_plan=False,
                tool_called=False,
                unavailable=True,
            )

        request: ToolRequest | None = None
        if decision.tool_call is not None:
            try:
                request = _REQUEST.validate_python(decision.tool_call)
            except ValidationError:
                request = None
        related = is_related(plan, request, decision.related_to_plan)

        if request is None:
            if decision.tool_call is not None:
                return QuickAnswer(
                    text="I could not work out which data that question needs. Could you rephrase it?",
                    from_plan=False,
                    related_to_plan=related,
                    tool_called=False,
                    unavailable=True,
                )
            text = decision.answer or "I don't have that information in your plan."
            return QuickAnswer(text=text, from_plan=plan is not None, related_to_plan=related, tool_called=False)

        if plan is not None and related:
            held = answer_from_plan(request, plan, self.deps)
            if held is not None:
                text, sources = held
                return QuickAnswer(
                    text=text, from_plan=True, related_to_plan=True, tool_called=False, sources=sources
                )

        gateway = self.deps.tools.scoped(path=PathName.ASK, agent=None, trace=trace)
        outcome = await gateway.call(request)  # exactly one tool call; no repair loop on this path
        note = "" if related or plan is None else " (Note: this is not part of your current plan.)"
        if not outcome.ok:
            return QuickAnswer(
                text=f"That information is unavailable right now ({outcome.failure or outcome.status}).{note}",
                from_plan=False,
                related_to_plan=related,
                tool_called=True,
                unavailable=True,
            )
        return QuickAnswer(
            text=answer_from_tool(outcome, request) + note,
            from_plan=False,
            related_to_plan=related,
            tool_called=True,
            sources=[source_of(outcome)],
        )
