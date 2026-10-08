"""Ticket agent: "collects train and flight times and flags places that need a reservation"."""

from __future__ import annotations

from backend.agents.geo import normalise_name
from backend.agents.preplanning.base import (
    AgentRun,
    PreplanningAgent,
    failure_reason,
    oldest_fetch,
    source_of,
)
from backend.agents.prompts import TICKET_TOOLS_SYSTEM, Purpose
from backend.schemas.agents import AgentResult, AgentTask, TicketData
from backend.schemas.common import AgentName, SectionStatus
from backend.schemas.tools import (
    ReservationCheckRequest,
    ReservationsPayload,
    TicketSearchRequest,
    TicketsPayload,
    ToolOperation,
    ToolOutcome,
    ToolRequest,
)
from backend.schemas.trip_plan import Disruption, DisruptionKind, Reservation, TicketOption


def _same_city(a: str | None, b: str | None) -> bool:
    return bool(a and b) and normalise_name(a or "") == normalise_name(b or "")


class TicketAgent(PreplanningAgent):
    name = AgentName.TICKET
    tools_purpose = Purpose.TICKET_TOOLS
    tools_system = TICKET_TOOLS_SYSTEM
    expected_operations = frozenset({ToolOperation.TICKET_SEARCH, ToolOperation.RESERVATION_CHECK})

    def canonical_requests(self, task: AgentTask) -> list[ToolRequest]:
        ctx = task.context
        if not ctx.destination:
            return []
        requests: list[ToolRequest] = []
        if ctx.origin and ctx.start_date and ctx.end_date and not _same_city(ctx.origin, ctx.destination):
            party = ctx.party_size or 1
            requests.append(
                TicketSearchRequest(
                    origin=ctx.origin,
                    destination=ctx.destination,
                    travel_date=ctx.start_date,
                    modes=["train", "flight"],
                    party_size=party,
                )
            )
            requests.append(
                TicketSearchRequest(
                    origin=ctx.destination,
                    destination=ctx.origin,
                    travel_date=ctx.end_date,
                    modes=["train", "flight"],
                    party_size=party,
                )
            )
        requests.append(ReservationCheckRequest(destination=ctx.destination))
        return requests

    async def _run(self, task: AgentTask, run: AgentRun) -> AgentResult:
        outcomes = await self.propose_and_call(task, run)
        ctx = task.context
        needs_tickets = bool(ctx.origin) and not _same_city(ctx.origin, ctx.destination)
        excluded = set(task.exclude_ids)

        outbound: list[TicketOption] = []
        inbound: list[TicketOption] = []
        reservations: list[Reservation] = []
        disruptions: list[Disruption] = []
        ticket_failures: list[ToolOutcome] = []
        notes: list[str] = []
        searched = False
        for outcome in outcomes:
            if outcome.operation is ToolOperation.TICKET_SEARCH:
                if not (outcome.ok and isinstance(outcome.payload, TicketsPayload)):
                    ticket_failures.append(outcome)
                    continue
                searched = True
                source = source_of(outcome)
                for record in outcome.payload.tickets:
                    if record.ticket_id in excluded:
                        continue
                    direction = "return" if _same_city(record.origin, ctx.destination) else "outbound"
                    option = TicketOption(**record.model_dump(), direction=direction, source=source)
                    (inbound if direction == "return" else outbound).append(option)
                    if record.status != "scheduled":
                        disruptions.append(
                            Disruption(
                                kind=DisruptionKind.TRANSIT_DELAY,
                                detail=(
                                    f"{record.carrier} {record.origin} to {record.destination} "
                                    f"{record.status}"
                                    + (f" by {record.delay_minutes} min" if record.delay_minutes else "")
                                ),
                                date=record.depart_at.date(),
                                affected_ids=[record.ticket_id],
                            )
                        )
            elif outcome.operation is ToolOperation.RESERVATION_CHECK:
                if outcome.ok and isinstance(outcome.payload, ReservationsPayload):
                    source = source_of(outcome)
                    reservations.extend(
                        Reservation(**r.model_dump(), source=source)
                        for r in outcome.payload.reservations
                    )
                else:
                    notes.append("reservation check unavailable")

        if needs_tickets and not searched:
            reason = failure_reason(ticket_failures[0]) if ticket_failures else "no ticket search made"
            return self.unavailable(reason)
        if not needs_tickets:
            notes.append("no origin outside the destination; tickets not searched")

        def order(options: list[TicketOption]) -> list[TicketOption]:
            return sorted(options, key=lambda t: (t.status != "scheduled", t.depart_at, t.ticket_id))

        return AgentResult(
            agent=self.name,
            status=SectionStatus.OK,
            data=TicketData(outbound=order(outbound), inbound=order(inbound), reservations=reservations),
            fetched_at=oldest_fetch(outcomes),
            disruptions=disruptions,
            reason="; ".join(notes) or None,
        )
