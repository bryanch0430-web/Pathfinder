"""Each city's share of a trip's budget, for planning a trip of several cities and for editing one.

The chosen transport that has a fare comes off the budget first (converted into the budget's currency); what is left
is shared per night among the cities. A leg with no fare (an estimated train, bus or drive, or a leg with no option)
takes nothing off: the cost notes say it is not costed.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from app.domain.models import TicketOption


def city_budget_shares(
    budget: float | None,
    currency: str,
    nights: Sequence[int],
    chosen: Iterable[TicketOption],
    fx: Any,
) -> list[float | None]:
    """The budget each city gets, in city order; all None when there is no budget.

    ``nights`` are the cities' nights in order and ``chosen`` the transport options the traveller has chosen.
    """
    if budget is None:
        return [None] * len(nights)
    spent = sum(
        fx.convert(ticket.price.amount, ticket.price.currency, currency)
        for ticket in chosen
        if ticket.price is not None
    )
    per_night = max(0.0, budget - spent) / max(sum(nights), 1)
    return [round(per_night * stay, 2) for stay in nights]
