"""Airport transfers through the API and through a planned trip."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from multi_city_rig import multi_city_rig, route_context
from plan_samples import make_two_city_plan
from test_airport_transfers import AirportGeocoder, FakeRoutes, _flights, _sync

from app.config import Settings
from app.domain.models import FlightCandidate, GeoPoint, Money, PriceKind
from app.eval_metering import RunLedger
from app.interface.api import create_app


def test_choosing_another_stay_through_the_api_works_the_transfer_out_again_and_says_so(
    settings: Settings,
) -> None:
    plan = _flights(make_two_city_plan())
    plan.hotel_options = [
        plan.hotels[0].model_copy(),
        plan.hotels[0].model_copy(
            update={
                "id": "hotel_a2",
                "name": "Far Out Inn",
                "geo": GeoPoint(lat=35.2, lng=135.9),
            }
        ),
    ]
    _sync(plan)
    plan.transfers_synced = True
    next(leg for leg in plan.legs if leg.id == "to_stay_seg_a").locked = True

    with TestClient(create_app(settings)) as client:
        container = client.app.state.container
        container.registry.geocoder = AirportGeocoder()
        container.registry.routes = [FakeRoutes(transit_minutes=70, drive_minutes=50)]
        session_id = client.post(
            "/api/journeys", json={"client_token": "guest-xfer"}
        ).json()["session"]["id"]
        plan.session_id = session_id
        container.store.save_plan(plan, "test plan")

        answer = client.post(
            f"/api/journeys/{session_id}/plan/choose",
            json={"kind": "hotel", "target_id": "hotel_a", "option_id": "hotel_a2"},
            headers={"X-Client-Token": "guest-xfer"},
        )

    assert answer.status_code == 200, answer.text
    data = answer.json()["plan"]
    leg = next(row for row in data["legs"] if row["id"] == "to_stay_seg_a")
    assert leg["to_place"] == "Far Out Inn" and leg["locked"] is False
    assert (
        "The airport transfer in Kyoto was worked out again for your new stay."
        in data["change_summary"]
    )


def test_a_planned_one_city_trip_with_a_real_flight_gets_both_transfers(
    settings: Settings,
) -> None:
    class Flights:
        name = "real-codes"
        available = True

        async def search(
            self, *, origin, destination, depart_date, return_date, party_size, currency
        ):
            depart = datetime.combine(depart_date, datetime.min.time()).replace(hour=8)
            back = datetime.combine(
                return_date or depart_date, datetime.min.time()
            ).replace(hour=17)
            return [
                FlightCandidate(
                    airline="Pathfinder Air",
                    flight_number="PF1",
                    origin="HKG",
                    destination="KIX",
                    depart_at=depart,
                    arrive_at=depart + timedelta(hours=4),
                    duration_minutes=240,
                    return_origin="KIX",
                    return_destination="HKG",
                    return_depart_at=back,
                    return_arrive_at=back + timedelta(hours=4),
                    price=Money(amount=900, currency="HKD"),
                    price_kind=PriceKind.REFERENCE,
                )
            ]

    class Mixed(AirportGeocoder):
        async def geocode(self, name: str, *, country_hint: str = ""):
            if name.endswith(" airport"):
                return await super().geocode(name, country_hint=country_hint)
            from app.evaluation import SandboxGeocoder

            return await SandboxGeocoder("Kyoto").geocode(name)

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
            registry = rig.plan_path.registry
            registry.flights = [Flights()]
            registry.geocoder = Mixed()
            registry.routes = [FakeRoutes()]
            rig.llm.route = {"intent": "plan", "confidence": 0.95}
            session = ledger.store.create_session("guest-xfer-plan")
            ledger.store.save_context(
                route_context(session.id, [("Kyoto", None)], nights=3)
            )
            _ = [
                e
                async for e in rig.turns.handle_turn(
                    session_id=session.id, message="Plan this trip"
                )
            ]
            return ledger.store.get_plan(session.id)
        finally:
            ledger.close()

    plan = asyncio.run(main())

    assert plan.transfers_synced is True
    assert [leg.kind for leg in plan.transfer_legs()] == ["to_stay", "from_stay"]
    assert all(
        plan.options_for(leg.id) and plan.options_for(leg.id)[0].selected
        for leg in plan.transfer_legs()
    )
    assert [leg.kind for leg in plan.route_legs()] == ["outbound"]
