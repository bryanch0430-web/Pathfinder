"""An offline rig for multi-city trips: the sandbox sources, but each one answers for the city it is asked about."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

from app.config import Settings
from app.domain.models import EvalCase, TripContext, TripStop
from app.eval_efficiency import Rig, build_rig, case_context
from app.eval_metering import RunLedger
from app.evaluation import SandboxGeocoder, SandboxHotels, SandboxPlaces

START = date(2027, 3, 1)


class CityGeocoder:
    name = "sandbox-city-geocoder"

    async def geocode(self, name: str, *, country_hint: str = ""):
        return await SandboxGeocoder(name).geocode(name)


class CityPlaces:
    name = "sandbox-city-places"

    async def search(self, *, destination, geo, category, limit, query=None):
        return await SandboxPlaces(destination).search(
            destination=destination,
            geo=geo,
            category=category,
            limit=limit,
            query=query,
        )


class CityHotels:
    name = "sandbox-city-hotels"

    async def search(self, **kwargs: Any):
        return await SandboxHotels(kwargs["destination"]).search(**kwargs)


def multi_city_rig(settings: Settings, ledger: RunLedger) -> Rig:
    """The efficiency rig with its geocoder, places and hotels answering per city."""
    rig = build_rig(settings, "Kyoto", ledger)
    registry = rig.plan_path.registry
    registry.geocoder = CityGeocoder()
    registry.places = [CityPlaces()]
    registry.hotels = [CityHotels()]
    return rig


def route_context(
    session_id: str,
    stops: list[tuple[str, int | None]],
    *,
    start: date = START,
    nights: int | None = None,
    **more: Any,
) -> TripContext:
    """A complete trip form for the stops; the trip runs ``nights`` nights (the sum of the stated nights by default)."""
    total = nights if nights is not None else sum(n or 0 for _, n in stops)
    case = EvalCase(
        id="c",
        dataset="d",
        input="",
        destination=stops[0][0],
        days=total + 1,
        party_size=2,
        budget=90000,
        currency="HKD",
    )
    context = case_context(case, session_id)
    context.start_date, context.end_date, context.days = (
        start,
        start + timedelta(days=total),
        total + 1,
    )
    context.stops = (
        [TripStop(city=city, nights=n) for city, n in stops] if len(stops) > 1 else []
    )
    for key, value in more.items():
        setattr(context, key, value)
    return context


def run_turn(
    settings: Settings, context: TripContext, message: str = "Plan this trip"
) -> tuple[list, Any, Any]:
    """One real planning turn on a stored form; (events, the stored plan, the ledger's store)."""
    settings = settings.model_copy(
        update={
            "preplan_budget_seconds": 15,
            "agent_timeout_seconds": 8,
            "enable_flights": True,
        }
    )

    async def main():
        ledger = RunLedger()
        try:
            rig = multi_city_rig(settings, ledger)
            rig.llm.route = {"intent": "plan", "confidence": 0.95}
            session = ledger.store.create_session("guest-route")
            context.session_id = session.id
            ledger.store.save_context(context)
            events = [
                event
                async for event in rig.turns.handle_turn(
                    session_id=session.id, message=message
                )
            ]
            return events, ledger.store.get_plan(session.id), ledger
        finally:
            ledger.close()

    return asyncio.run(main())
