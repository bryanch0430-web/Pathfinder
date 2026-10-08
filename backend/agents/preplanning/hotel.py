"""Hotel agent: "searches by budget and style and ranks candidates by rating and distance"."""

from __future__ import annotations

import asyncio

from backend.agents.geo import haversine_km
from backend.agents.preplanning.base import (
    AgentRun,
    PreplanningAgent,
    failure_reason,
    oldest_fetch,
    source_of,
)
from backend.agents.prompts import HOTEL_TOOLS_SYSTEM, Purpose
from backend.schemas.agents import AgentResult, AgentTask, HotelData
from backend.schemas.common import AgentName, GeoPoint, SectionStatus
from backend.schemas.trip import TripContext
from backend.schemas.tools import (
    GeocodePayload,
    GeocodeRequest,
    PlaceRecord,
    PlacesPayload,
    PlacesSearchRequest,
    ToolOperation,
    ToolOutcome,
    ToolRequest,
)
from backend.schemas.trip_plan import Hotel

# Share of the total budget the hotel may take when deriving a nightly cap (DECISIONS.md).
HOTEL_BUDGET_SHARE = 0.45
DISTANCE_WEIGHT_PER_KM = 0.25
STYLE_BONUS = 0.3
OVER_CAP_PENALTY = 1.0


def nightly_cap(context: TripContext) -> float | None:
    if not (context.budget and context.days):
        return None
    nights = max(1, context.days - 1)
    return round(context.budget.amount * HOTEL_BUDGET_SHARE / nights, 2)


def rank_score(hotel: Hotel, *, cap: float | None, style: str | None) -> float:
    score = hotel.rating if hotel.rating is not None else 3.0
    if hotel.distance_km is not None:
        score -= DISTANCE_WEIGHT_PER_KM * hotel.distance_km
    if style and hotel.style and hotel.style.casefold() == style.casefold():
        score += STYLE_BONUS
    if cap is not None and hotel.nightly_price.amount > cap:
        score -= OVER_CAP_PENALTY
    return score


class HotelAgent(PreplanningAgent):
    name = AgentName.HOTEL
    tools_purpose = Purpose.HOTEL_TOOLS
    tools_system = HOTEL_TOOLS_SYSTEM
    expected_operations = frozenset({ToolOperation.PLACES_SEARCH})

    def canonical_requests(self, task: AgentTask) -> list[ToolRequest]:
        ctx = task.context
        if not ctx.destination:
            return []
        return [
            PlacesSearchRequest(
                destination=ctx.destination,
                category="hotel",
                max_price=nightly_cap(ctx),
                currency=ctx.budget.currency if ctx.budget else None,
                style=ctx.hotel_style or task.preferences.hotel_style,
                limit=10,
            )
        ]

    async def _run(self, task: AgentTask, run: AgentRun) -> AgentResult:
        outcomes = await self.propose_and_call(task, run)
        searches = [o for o in outcomes if o.ok and isinstance(o.payload, PlacesPayload)]
        if not searches:
            failed = [o for o in outcomes if not o.ok]
            return self.unavailable(failure_reason(failed[0]) if failed else "no hotel search made")

        destination = task.context.destination or ""
        excluded = set(task.exclude_ids)
        records: list[tuple[PlaceRecord, ToolOutcome]] = []
        for outcome in searches:
            assert isinstance(outcome.payload, PlacesPayload)
            for record in outcome.payload.places:
                if record.place_id in excluded or record.price is None:
                    continue
                records.append((record, outcome))

        centre_outcome = await self.call(run, GeocodeRequest(query=destination, city=destination))
        centre: GeoPoint | None = (
            centre_outcome.payload.result.location
            if centre_outcome.ok and isinstance(centre_outcome.payload, GeocodePayload)
            else None
        )
        missing = [r for r, _ in records if r.location is None]
        geocoded = await asyncio.gather(
            *(self.call(run, GeocodeRequest(query=r.name, city=destination)) for r in missing)
        )
        located: dict[str, GeoPoint] = {
            r.place_id: o.payload.result.location
            for r, o in zip(missing, geocoded, strict=True)
            if o.ok and isinstance(o.payload, GeocodePayload)
        }

        cap = nightly_cap(task.context)
        style = task.context.hotel_style or task.preferences.hotel_style
        hotels: list[Hotel] = []
        for record, outcome in records:
            assert record.price is not None
            location = record.location or located.get(record.place_id)
            hotels.append(
                Hotel(
                    hotel_id=record.place_id,
                    name=record.name,
                    location=location,
                    address=record.address,
                    nightly_price=record.price,
                    rating=record.rating,
                    style=record.style,
                    distance_km=round(haversine_km(location, centre), 2) if location and centre else None,
                    source=source_of(outcome),
                )
            )
        hotels.sort(key=lambda h: (-rank_score(h, cap=cap, style=style), h.hotel_id))
        return AgentResult(
            agent=self.name,
            status=SectionStatus.OK,
            data=HotelData(candidates=hotels),
            fetched_at=oldest_fetch(searches),
            reason=None if hotels else "no hotel matched the search",
        )
