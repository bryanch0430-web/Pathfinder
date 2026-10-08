"""Live ticket / reservation provider — PROVISIONAL (team decision 5: vendors are provisional)."""

from __future__ import annotations

from backend.schemas.tools import (
    ReservationCheckRequest,
    ReservationRecord,
    TicketRecord,
    TicketSearchRequest,
)


class LiveTicketProvider:
    name = "live-tickets"

    async def search(self, request: TicketSearchRequest) -> list[TicketRecord]:
        # TODO(provisional): ticket vendor not chosen (e.g. Amadeus Flight Offers for flights,
        # a rail aggregator such as JR / Klook for trains, MTR status feed for delays).
        raise NotImplementedError("TODO(provisional): live ticket search is not implemented")

    async def reservations(self, request: ReservationCheckRequest) -> list[ReservationRecord]:
        # TODO(provisional): venue reservation lead times (vendor not chosen; e.g. venue booking
        # pages or a reservations aggregator).
        raise NotImplementedError("TODO(provisional): live reservation check is not implemented")
