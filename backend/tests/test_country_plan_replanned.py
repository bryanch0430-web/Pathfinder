"""A plan stored for a country (before cities were planned) is planned again by city when the form names cities."""

from __future__ import annotations

import asyncio

from multi_city_rig import multi_city_rig, route_context
from plan_samples import make_plan

from app.application.router import REPLAN_MESSAGE
from app.config import Settings
from app.eval_metering import RunLedger


def test_generate_again_with_cities_on_a_country_plan_plans_the_route(settings: Settings) -> None:
    settings = settings.model_copy(
        update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8, "enable_flights": True}
    )

    async def main():
        ledger = RunLedger()
        try:
            rig = multi_city_rig(settings, ledger)
            session = ledger.store.create_session("guest-country")
            context = route_context(session.id, [("Tokyo", 2), ("Kyoto", 2)])
            japan = make_plan()
            japan.session_id = session.id
            japan.destination.name = "Japan"
            japan.segments[0].destination.name = "Japan"
            ledger.store.save_context(context)
            ledger.store.save_plan(japan, "Stored before the cities were planned")
            events = [e async for e in rig.turns.handle_turn(session_id=session.id, message=REPLAN_MESSAGE)]
            return events, ledger.store.get_plan(session.id)
        finally:
            ledger.close()

    _, plan = asyncio.run(main())

    assert [s.destination.name for s in plan.segments] == ["Tokyo", "Kyoto"]
    assert len(plan.hotels) == 2 and {h.segment_id for h in plan.hotels} == {s.id for s in plan.segments}
    assert plan.legs, "the way in and out are planned"
