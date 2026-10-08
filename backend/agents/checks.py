"""Check step of "check and merge": dates, budget, route continuity, ticket conflicts.

Proposal: "The same check is used when changing dates and budget, reviewing route continuity
and resolving ticket conflicts before returning the travel plan to users." These are pure
functions over the plan, so the planner, the modify path and the evaluation harness all apply
exactly the same rules.
"""

from __future__ import annotations

from backend.schemas.common import AgentName, SectionStatus
from backend.schemas.trip import TripContext
from backend.schemas.trip_plan import CheckName, ConstraintViolation, TripPlan


def check_dates(plan: TripPlan, context: TripContext) -> list[ConstraintViolation]:
    out: list[ConstraintViolation] = []
    if context.start_date and context.end_date and (
        plan.start_date != context.start_date or plan.end_date != context.end_date
    ):
        out.append(
            ConstraintViolation(
                check=CheckName.DATES,
                message=f"plan covers {plan.start_date}..{plan.end_date}, requested "
                f"{context.start_date}..{context.end_date}",
            )
        )
    if plan.hotel and plan.end_date > plan.start_date and (
        plan.hotel.check_in != plan.start_date or plan.hotel.check_out != plan.end_date
    ):
        out.append(
            ConstraintViolation(
                check=CheckName.DATES,
                message="hotel stay does not cover the trip nights",
                item_ids=[plan.hotel.hotel.hotel_id],
            )
        )
    for ticket in plan.tickets:
        expected = plan.start_date if ticket.direction == "outbound" else plan.end_date
        if ticket.depart_at.date() != expected:
            out.append(
                ConstraintViolation(
                    check=CheckName.DATES,
                    message=f"{ticket.direction} ticket departs {ticket.depart_at.date()}, trip "
                    f"needs {expected}",
                    item_ids=[ticket.ticket_id],
                )
            )
    return out


def check_budget(plan: TripPlan) -> list[ConstraintViolation]:
    if plan.budget is None or plan.cost is None:
        return []
    if plan.cost.currency != plan.budget.currency:
        return [
            ConstraintViolation(
                check=CheckName.BUDGET,
                message=f"cost in {plan.cost.currency} cannot be compared with a "
                f"{plan.budget.currency} budget (no exchange-rate source)",
            )
        ]
    if plan.cost.total > plan.budget.amount:
        return [
            ConstraintViolation(
                check=CheckName.BUDGET,
                message=f"estimated cost {plan.cost.total:.0f} {plan.cost.currency} exceeds "
                f"budget {plan.budget.amount:.0f} {plan.budget.currency}",
            )
        ]
    return []


def check_route(plan: TripPlan) -> list[ConstraintViolation]:
    out: list[ConstraintViolation] = []
    attractions = plan.section(AgentName.ATTRACTION)
    attraction_ok = attractions is None or attractions.status is SectionStatus.OK
    for day in plan.days:
        warning = day.forecast.warning_signal if day.forecast else None
        previous_end = None
        for item in day.items:
            place = plan.place(item.place_id)
            if place is None:
                continue
            if attraction_ok and place.location is None:
                out.append(
                    ConstraintViolation(
                        check=CheckName.ROUTE,
                        message=f"{place.name} has no location; route cannot be checked",
                        item_ids=[item.item_id],
                    )
                )
            if day.date in place.closed_dates:
                out.append(
                    ConstraintViolation(
                        check=CheckName.ROUTE,
                        message=f"{place.name} is closed on {day.date}",
                        item_ids=[item.item_id],
                    )
                )
            if warning and place.indoor is False:
                out.append(
                    ConstraintViolation(
                        check=CheckName.ROUTE,
                        message=f"outdoor stop {place.name} on {day.date} under warning {warning}",
                        item_ids=[item.item_id],
                    )
                )
            if previous_end and item.start_time and item.start_time < previous_end:
                out.append(
                    ConstraintViolation(
                        check=CheckName.ROUTE,
                        message=f"{item.title} overlaps the previous stop on {day.date}",
                        item_ids=[item.item_id],
                    )
                )
            previous_end = item.end_time or previous_end
    return out


def check_tickets(plan: TripPlan) -> list[ConstraintViolation]:
    out: list[ConstraintViolation] = []
    outbound = [t for t in plan.tickets if t.direction == "outbound"]
    inbound = [t for t in plan.tickets if t.direction == "return"]
    if len(outbound) > 1 or len(inbound) > 1:
        out.append(
            ConstraintViolation(
                check=CheckName.TICKETS,
                message="more than one ticket selected for the same leg",
                item_ids=[t.ticket_id for t in plan.tickets],
            )
        )
    for ticket in plan.tickets:
        if ticket.status != "scheduled":
            out.append(
                ConstraintViolation(
                    check=CheckName.TICKETS,
                    message=f"selected ticket {ticket.ticket_id} is {ticket.status}",
                    item_ids=[ticket.ticket_id],
                )
            )
    if outbound and inbound and outbound[0].arrive_at >= inbound[0].depart_at:
        out.append(
            ConstraintViolation(
                check=CheckName.TICKETS,
                message="return departs before the outbound arrives",
                item_ids=[outbound[0].ticket_id, inbound[0].ticket_id],
            )
        )
    first_day = plan.days[0]
    last_day = plan.days[-1]
    for ticket in outbound:
        first = next((i for i in first_day.items if i.start_time), None)
        if first and first.start_time and ticket.arrive_at.date() == first_day.date and (
            ticket.arrive_at.time() > first.start_time
        ):
            out.append(
                ConstraintViolation(
                    check=CheckName.TICKETS,
                    message=f"first stop starts before arrival at {ticket.arrive_at.time()}",
                    item_ids=[ticket.ticket_id, first.item_id],
                )
            )
    for ticket in inbound:
        timed = [i for i in last_day.items if i.end_time]
        last = timed[-1] if timed else None
        if last and last.end_time and ticket.depart_at.date() == last_day.date and (
            ticket.depart_at.time() < last.end_time
        ):
            out.append(
                ConstraintViolation(
                    check=CheckName.TICKETS,
                    message=f"last stop ends after the return departs at {ticket.depart_at.time()}",
                    item_ids=[ticket.ticket_id, last.item_id],
                )
            )
    return out


def check_plan(plan: TripPlan, context: TripContext) -> list[ConstraintViolation]:
    return [
        *check_dates(plan, context),
        *check_budget(plan),
        *check_route(plan),
        *check_tickets(plan),
    ]
