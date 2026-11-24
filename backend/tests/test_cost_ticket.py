from __future__ import annotations

from app.domain.models import Money, TicketOption, TripContext
from app.plan.builder import _cost, chosen_ticket


def _ticket(ident: str, amount: float, selected: bool = False) -> TicketOption:
    return TicketOption(
        id=ident, mode="flight", name=ident, selected=selected, price=Money(amount=amount, currency="HKD")
    )


def _travel_line(tickets):
    context = TripContext(session_id="s1", destination="Osaka", currency="HKD", party_size=2, budget=20000)
    cost = _cost(context, [], [], tickets, 2)
    return next(line.amount for line in cost.lines if line.kind == "flight")


def test_cost_uses_the_flight_the_traveller_picked():
    tickets = [_ticket("cheap", 3942), _ticket("picked", 5504, selected=True)]
    assert _travel_line(tickets) == 5504


def test_cost_falls_back_to_the_first_priced_flight():
    tickets = [_ticket("a", 3942), _ticket("b", 5504)]
    assert _travel_line(tickets) == 3942
    assert chosen_ticket([]) is None


def test_an_unpriced_pick_does_not_zero_the_travel_line():
    unpriced = TicketOption(id="x", mode="flight", name="x", selected=True)
    assert _travel_line([unpriced, _ticket("b", 5504)]) == 5504
