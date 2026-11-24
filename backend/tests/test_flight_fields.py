from __future__ import annotations

from datetime import date

from app.config import Settings
from app.domain.models import TripContext
from app.evidence.models import Bundle
from app.evidence.providers import Providers
from app.plan.builder import _tickets
from app.understand.schemas import PlanDraft


def _leg(src: str, dst: str, depart: str, arrive: str, minutes: int, number: str) -> dict:
    return {
        "departure_airport": {"id": src, "time": depart},
        "arrival_airport": {"id": dst, "time": arrive},
        "duration": minutes,
        "airline": "Test Air",
        "flight_number": number,
    }


OUTBOUND = {
    "best_flights": [
        {
            # Clock times are local to each airport: 11:50 to 16:25 is 4 h 35 min on the wall, 3 h 35 min in the air.
            "flights": [_leg("HKG", "KIX", "2026-12-12 11:50", "2026-12-12 16:25", 215, "UO 898")],
            "total_duration": 215,
            "price": 5142,
        },
        {
            "flights": [
                _leg("HKG", "TPE", "2026-12-12 08:00", "2026-12-12 09:50", 110, "CI 600"),
                _leg("TPE", "KIX", "2026-12-12 11:30", "2026-12-12 15:00", 150, "CI 100"),
            ],
            "layovers": [{"duration": 100, "id": "TPE"}],
            "total_duration": 360,
            "price": 6000,
        },
    ]
}
RETURN = {
    "best_flights": [
        {
            "flights": [_leg("KIX", "HKG", "2026-12-14 21:15", "2026-12-15 00:55", 280, "UO 863")],
            "total_duration": 280,
            "price": 2608,
        }
    ]
}


async def test_live_flight_duration_comes_from_the_fare_source_not_the_flight_number() -> None:
    providers = Providers(Settings(_env_file=None, SERPAPI_KEY="test-key", ENABLE_FLIGHTS=True, PATHFINDER_SANDBOX=False))
    calls: list[dict] = []

    async def fake_serp(params: dict, *, purpose: str) -> dict:
        calls.append(params)
        return RETURN if params.get("type") == 2 else OUTBOUND

    providers._serp = fake_serp  # type: ignore[method-assign]
    rides = await providers.tickets("Hong Kong", "Osaka", date(2026, 12, 12), date(2026, 12, 14), 2)
    assert [ride.duration_minutes for ride in rides] == [215, 360]
    assert [ride.stops for ride in rides] == [0, 1]
    assert rides[0].return_duration_minutes == 280
    assert rides[0].return_stops == 0

    bundle = Bundle()
    bundle.items.extend(rides)
    context = TripContext(session_id="s1", origin="Hong Kong", destination="Osaka", party_size=2)
    options = _tickets(context, bundle, PlanDraft(), date(2026, 12, 12), date(2026, 12, 14))
    assert options[0].duration_minutes == 215  # the flight number 898 is not minutes
    assert options[0].return_duration_minutes == 280
    assert options[1].stops == 1
