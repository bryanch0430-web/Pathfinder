import asyncio
from datetime import date

from app.application.legs import legs_for_plan
from app.config import Settings
from app.domain.models import (
    DateRange,
    DayPlan,
    Destination,
    GeoPoint,
    HotelStay,
    PlanItem,
    RouteLeg,
    SourceRef,
    TripPlan,
)


def _source(name: str) -> list[SourceRef]:
    return [SourceRef(title=name, url="https://example.test/" + name, source="test")]


class FakeRoutes:
    """No network: returns the straight line so tests stay deterministic."""

    available = True

    async def leg(self, origin, destination, *, mode="walk"):
        return RouteLeg(
            from_geo=origin,
            to_geo=destination,
            duration_minutes=11,
            distance_km=1.4,
            mode=mode,
            source="fake",
            polyline=[origin, destination],
        )


def _plan(hotel_geo: GeoPoint | None) -> TripPlan:
    return TripPlan(
        session_id="s",
        destination=Destination(name="Kyoto", geo=GeoPoint(lat=35.0, lng=135.75)),
        dates=DateRange(start=date(2027, 4, 1), end=date(2027, 4, 1)),
        days=[
            DayPlan(
                date=date(2027, 4, 1),
                title="Day one",
                items=[
                    PlanItem(
                        id="a1",
                        type="attraction",
                        name="Shirakawa Canal",
                        start="08:00",
                        end="10:00",
                        source_refs=_source("shirakawa"),
                        geo=GeoPoint(lat=35.0054, lng=135.7736),
                    ),
                    PlanItem(
                        id="m1",
                        type="meal",
                        name="Lunch Place",
                        start="12:30",
                        end="13:30",
                        geo=GeoPoint(lat=35.0042, lng=135.7745),
                    ),
                    PlanItem(
                        id="a2",
                        type="attraction",
                        name="Kiyomizu-dera",
                        start="13:45",
                        end="15:45",
                        source_refs=_source("kiyomizu"),
                        geo=GeoPoint(lat=34.9947, lng=135.7847),
                    ),
                ],
            )
        ],
        hotels=[
            HotelStay(
                id="hotel_x",
                name="Stay",
                check_in=date(2027, 4, 1),
                check_out=date(2027, 4, 2),
                geo=hotel_geo,
            )
        ],
    )


def test_legs_connect_only_the_scheduled_stops(settings: Settings) -> None:
    plan = _plan(GeoPoint(lat=35.0, lng=135.75))
    payload = asyncio.run(
        legs_for_plan(plan, routes=[FakeRoutes()], settings=settings, day="2027-04-01")
    )
    pairs = [(leg["from_id"], leg["to_id"]) for leg in payload["legs"]]
    # The itinerary does not schedule hotel transfer, so the map must not invent
    # a walk to/from the stay.
    assert pairs == [
        ("a1", "m1"),
        ("m1", "a2"),
    ]
    assert all(leg["source"] == "fake" for leg in payload["legs"])


def test_a_far_away_hotel_is_not_linked_as_a_local_leg(settings: Settings) -> None:
    plan = _plan(GeoPoint(lat=40.7, lng=-74.0))  # New York stay for a Kyoto day
    payload = asyncio.run(
        legs_for_plan(plan, routes=[FakeRoutes()], settings=settings, day="2027-04-01")
    )
    pairs = [(leg["from_id"], leg["to_id"]) for leg in payload["legs"]]
    assert pairs == [("a1", "m1"), ("m1", "a2")]


def test_estimate_is_used_when_no_route_provider_is_available(settings: Settings) -> None:
    plan = _plan(GeoPoint(lat=35.0, lng=135.75))
    payload = asyncio.run(legs_for_plan(plan, routes=[], settings=settings, day=None))
    assert payload["legs"]
    assert all(leg["source"] == "estimate" for leg in payload["legs"])


def test_map_estimate_uses_the_itinerary_transport_model(settings: Settings) -> None:
    """Without a live routes source the map shows the plan's own transfer minutes."""
    from app.domain.models import TransferLeg
    from app.infrastructure.http import haversine_km
    from app.infrastructure.providers.routes import model_travel

    plan = _plan(None)
    first, lunch, second = plan.days[0].items
    second.transfer = TransferLeg(
        from_item_id=lunch.id, minutes=17, mode="metro", source="transport-model", tool="routes.segments", result_id="x:1-2"
    )
    payload = asyncio.run(legs_for_plan(plan, routes=[], settings=settings, day=None))
    legs = {(leg["from_id"], leg["to_id"]): leg for leg in payload["legs"]}
    assert (legs[(lunch.id, second.id)]["minutes"], legs[(lunch.id, second.id)]["mode"]) == (17, "transit")
    km = haversine_km(first.geo.lat, first.geo.lng, lunch.geo.lat, lunch.geo.lng)
    mode, minutes = model_travel(km)
    assert (legs[(first.id, lunch.id)]["minutes"], legs[(first.id, lunch.id)]["mode"], mode) == (minutes, "walk", "walk")
