"""Hotel and flight options on the plan, and choosing an option in the UI with no model call.

No network and no LLM: SerpApi answers come from fixtures through a patched ``request_json``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.infrastructure.providers.hotels as hotels_module
from app.application.planner import Planner
from app.config import Settings
from app.domain.models import (
    CostBreakdown,
    DateRange,
    DayPlan,
    Destination,
    GeoPoint,
    HotelStay,
    ItemType,
    Money,
    PlanAlternative,
    PlanItem,
    PriceKind,
    Reservation,
    SourceRef,
    TicketOption,
    TripPlan,
    utcnow,
)
from app.interface.api import create_app
from app.infrastructure.providers.hotels import OVER_BUDGET_NOTE, SerpApiHotelSource, parse_rooms

GUEST = {"X-Client-Token": "guest-choose"}
START = date(2027, 4, 10)


def _ref(name: str) -> SourceRef:
    return SourceRef(title=name, url=f"https://example.test/{name.replace(' ', '-')}", source="google-places", fetched_at=utcnow())


def _row(name: str, price: float, rating: float, **extra: Any) -> dict:
    return {
        "name": name,
        "gps_coordinates": {"latitude": 35.0, "longitude": 135.76},
        "rate_per_night": {"extracted_lowest": price},
        "overall_rating": rating,
        "reviews": 100,
        "link": f"https://hotel.test/{name.replace(' ', '-')}",
        **extra,
    }


@pytest.fixture()
def serp(settings: Settings, monkeypatch: pytest.MonkeyPatch):
    answers: dict[str, Any] = {"properties": []}
    calls: list[dict] = []

    async def fake_request_json(*_: Any, **kwargs: Any) -> dict:
        calls.append(kwargs.get("params") or {})
        return answers.get("details") if (kwargs.get("params") or {}).get("property_token") else answers

    monkeypatch.setattr(hotels_module, "request_json", fake_request_json)
    source = SerpApiHotelSource(settings.model_copy(update={"serpapi_key": "test-key"}))
    return source, answers, calls


def _search(source: SerpApiHotelSource, budget: float | None) -> list:
    return asyncio.run(
        source.search(
            destination="Kyoto", check_in=START, check_out=START + timedelta(days=3), party_size=2,
            budget=budget, currency="USD",
        )
    )


# --- hotel facts from the list ------------------------------------------------


def test_hotel_facts_are_kept_as_the_source_gave_them_and_never_invented(serp) -> None:
    source, answers, _ = serp
    answers["properties"] = [
        _row(
            "Full Facts Hotel", 120, 4.6,
            amenities=["Free Wi-Fi", "Pool"], hotel_class="4-star hotel", extracted_hotel_class=4,
            property_token="tok-1",
            nearby_places=[{"name": "Kyoto Station", "transportations": [{"type": "Public transport", "duration": "6 min"}]}],
        ),
        _row("Bare Hotel", 90, 4.0),
        _row("Class Text Hotel", 100, 4.1, hotel_class="3-star hotel"),
    ]
    found = {hotel.name: hotel for hotel in _search(source, None)}
    full = found["Full Facts Hotel"]
    assert full.amenities == ["Free Wi-Fi", "Pool"]
    assert full.hotel_class == 4
    assert full.property_token == "tok-1"
    assert [(row.name, row.type, row.duration) for row in full.nearby] == [("Kyoto Station", "Public transport", "6 min")]
    bare = found["Bare Hotel"]
    assert bare.amenities == [] and bare.hotel_class is None and bare.property_token == "" and bare.nearby == []
    assert found["Class Text Hotel"].hotel_class == 3


def test_the_over_budget_rule_still_keeps_the_three_cheapest_flagged(serp) -> None:
    source, answers, _ = serp
    answers["properties"] = [_row(f"Hotel {price}", price, 4.0, amenities=["Spa"]) for price in (900, 700, 500, 1200, 650)]
    kept = _search(source, budget=300)
    assert [hotel.nightly_price.amount for hotel in kept] == [500, 650, 700]
    assert all(hotel.notes == OVER_BUDGET_NOTE for hotel in kept)
    assert all(hotel.amenities == ["Spa"] for hotel in kept)


def test_the_plan_keeps_up_to_five_stays_with_the_chosen_one_first() -> None:
    rows = [
        {"id": f"h{index}", "name": f"Hotel {index}", "nightly_price": {"amount": 100 + index, "currency": "USD"},
         "price_kind": "reference", "amenities": ["Wi-Fi"] if index == 2 else [], "hotel_class": 3 if index == 2 else None,
         "property_token": f"tok-{index}"}
        for index in range(1, 7)
    ]
    dates = DateRange(start=START, end=START + timedelta(days=2))
    chosen = Planner._build_hotel(rows[2], dates)
    options = Planner.hotel_options(rows, chosen, dates)
    assert len(options) == 5
    assert options[0] is chosen
    assert len({stay.id for stay in options}) == 5
    second = next(stay for stay in options if stay.name == "Hotel 2")
    assert second.amenities == ["Wi-Fi"] and second.hotel_class == 3 and second.property_token == "tok-2"
    assert next(stay for stay in options if stay.name == "Hotel 1").amenities == []


# --- rooms ---------------------------------------------------------------------

DETAILS = {
    "name": "Full Facts Hotel",
    "featured_prices": [
        {
            "source": "Booking.com",
            "link": "https://book.test/offer",
            "rate_per_night": {"lowest": "$120", "extracted_lowest": 120},
            "rooms": [
                {"name": "Standard Double", "link": "https://book.test/std", "rate_per_night": {"extracted_lowest": 120}},
                {"name": "Deluxe Twin", "rate_per_night": {"lowest": "$155"}},
                {"name": "Standard Double", "rate_per_night": {"extracted_lowest": 120}},
            ],
        }
    ],
    "prices": [{"source": "Agoda", "rate_per_night": {"extracted_lowest": 118}}],
}


def test_rooms_are_read_from_the_featured_prices() -> None:
    rooms = parse_rooms(DETAILS, "USD")
    assert [(room.name, room.source, room.price_per_night.amount if room.price_per_night else None) for room in rooms] == [
        ("Standard Double", "Booking.com", 120.0),
        ("Deluxe Twin", "Booking.com", 155.0),
    ]
    assert rooms[0].link == "https://book.test/std"
    assert rooms[1].link == "https://book.test/offer"
    assert parse_rooms({"prices": [{"source": "Agoda"}]}, "USD") == []
    assert parse_rooms({}, "USD") == []


# --- choosing on a saved plan --------------------------------------------------


def _alt(name: str, lat: float, **extra: Any) -> PlanAlternative:
    return PlanAlternative(id=f"alt_{name.split()[0].lower()}", name=name, geo=GeoPoint(lat=lat, lng=135.77),
                           source_refs=[_ref(name)], **extra)


def _plan(session_id: str) -> TripPlan:
    days = []
    for offset in range(3):
        day = START + timedelta(days=offset)
        items = [
            PlanItem(id=f"a{offset}", type=ItemType.ATTRACTION, name=f"Temple {offset}", start="10:00", end="12:00",
                     geo=GeoPoint(lat=35.00 + offset * 0.01, lng=135.77), source_refs=[_ref(f"Temple {offset}")]),
            PlanItem(id=f"m{offset}", type=ItemType.MEAL, name=f"Lunch Place {offset}", start="12:30", end="13:30",
                     geo=GeoPoint(lat=35.002 + offset * 0.01, lng=135.77), source_refs=[_ref(f"Lunch Place {offset}")],
                     tags=["meal"], meal_slot="lunch", rating=4.2, cuisine="Ramen",
                     alternatives=[_alt(f"Udon{offset} House", 35.01 + offset * 0.01, rating=4.5, reservable=True),
                                   _alt(f"Soba{offset} Bar", 35.003 + offset * 0.01)]),
            PlanItem(id=f"b{offset}", type=ItemType.ATTRACTION, name=f"Garden {offset}", start="14:30", end="16:00",
                     geo=GeoPoint(lat=35.004 + offset * 0.01, lng=135.775), source_refs=[_ref(f"Garden {offset}")]),
        ]
        days.append(DayPlan(date=day, items=items))
    end = START + timedelta(days=2)

    def stay(index: int, price: float) -> HotelStay:
        return HotelStay(id=f"hotel_h{index}", name=f"Hotel {index}", geo=GeoPoint(lat=35.0, lng=135.76),
                         check_in=START, check_out=end, price_per_night=Money(amount=price, currency="USD"),
                         price_kind=PriceKind.REFERENCE, property_token=f"tok-{index}", amenities=["Wi-Fi"])

    hotels = [stay(1, 100), stay(2, 300), stay(3, 80)]

    def ticket(index: int, price: float, arrive: str) -> TicketOption:
        return TicketOption(id=f"flight_f{index}", mode="flight", name=f"Air {index}", origin="HKG", destination="KIX",
                            depart_at=datetime.fromisoformat(f"{START}T06:00"), arrive_at=datetime.fromisoformat(f"{START}T{arrive}"),
                            price=Money(amount=price, currency="USD"), price_kind=PriceKind.REFERENCE,
                            booking_url=f"https://air.test/{index}")

    plan = TripPlan(
        session_id=session_id,
        destination=Destination(name="Kyoto", geo=GeoPoint(lat=35.01, lng=135.77)),
        dates=DateRange(start=START, end=end),
        party_size=2,
        days=days,
        hotels=[hotels[0]],
        hotel_options=hotels,
        tickets=[ticket(1, 500, "07:00"), ticket(2, 350, "15:00")],
        cost=CostBreakdown(currency="USD", total=700, budget=1000),
        reservations=[Reservation(name="Flight · Air 1", reason="ref", url="https://air.test/1", status="optional")],
    )
    return plan


@pytest.fixture()
def client(settings: Settings, monkeypatch: pytest.MonkeyPatch):
    with TestClient(create_app(settings)) as test_client:
        gateway = test_client.app.state.container.gateway

        async def no_model(*_: Any, **__: Any):
            raise AssertionError("choosing an option must not call the model")

        monkeypatch.setattr(gateway, "json", no_model)
        monkeypatch.setattr(gateway, "text", no_model)
        yield test_client


def _saved(client: TestClient) -> tuple[str, TripPlan]:
    session_id = client.post("/api/journeys", json={"client_token": "guest-choose"}).json()["session"]["id"]
    plan = _plan(session_id)
    client.app.state.container.store.save_plan(plan, "test plan")
    return session_id, plan


def _choose(client: TestClient, session_id: str, **body: Any):
    return client.post(f"/api/journeys/{session_id}/plan/choose", json=body, headers=GUEST)


def test_choosing_a_meal_alternative_swaps_it_and_keeps_the_old_default_as_an_alternative(client: TestClient) -> None:
    session_id, plan = _saved(client)
    answer = _choose(client, session_id, kind="item", target_id="m1", option_id="alt_udon1")
    assert answer.status_code == 200, answer.text
    data = answer.json()["plan"]
    assert data["version"] == plan.version + 1
    item = next(row for row in data["days"][1]["items"] if row["id"] == "m1")
    assert item["name"] == "Udon1 House"
    assert (item["start"], item["end"], item["type"], item["meal_slot"]) == ("12:30", "13:30", "meal", "lunch")
    assert item["rating"] == 4.5 and item["reservable"] is True and item["cuisine"] is None
    names = [alt["name"] for alt in item["alternatives"]]
    assert names == ["Lunch Place 1", "Soba1 Bar"]
    old = item["alternatives"][0]
    assert old["rating"] == 4.2 and old["cuisine"] == "Ramen"
    assert all(isinstance(alt["distance_m"], int) for alt in item["alternatives"])
    # The day's legs are the app's own, logged from the transport model.
    assert item["transfer"]["from_item_id"] == "a1" and item["transfer"]["tool"] == "routes.segments"
    after = next(row for row in data["days"][1]["items"] if row["id"] == "b1")
    assert after["transfer"]["from_item_id"] == "m1"
    # Saved as the next version; the other days are untouched.
    stored = client.app.state.container.store.get_plan(session_id)
    assert stored.version == plan.version + 1 and stored.days[1].items[1].name == "Udon1 House"
    assert stored.days[0].items[1].name == "Lunch Place 0"
    # And the old default can be chosen back.
    back = _choose(client, session_id, kind="item", target_id="m1", option_id="alt_m1")
    assert back.status_code == 200, back.text
    again = next(row for row in back.json()["plan"]["days"][1]["items"] if row["id"] == "m1")
    assert again["name"] == "Lunch Place 1"
    assert "Udon1 House" in [alt["name"] for alt in again["alternatives"]]


def test_choosing_a_hotel_makes_it_the_stay_and_recosts(client: TestClient) -> None:
    session_id, _ = _saved(client)
    answer = _choose(client, session_id, kind="hotel", option_id="hotel_h3")
    assert answer.status_code == 200, answer.text
    data = answer.json()["plan"]
    assert data["hotels"][0]["id"] == "hotel_h3"
    assert (data["hotels"][0]["check_in"], data["hotels"][0]["check_out"]) == (START.isoformat(), (START + timedelta(days=2)).isoformat())
    assert {stay["id"] for stay in data["hotel_options"]} == {"hotel_h1", "hotel_h2", "hotel_h3"}
    hotel_line = next(line for line in data["cost"]["lines"] if line["label"].startswith("Hotel"))
    assert "Hotel 3" in hotel_line["label"] and hotel_line["amount"] == 160.0
    assert data["hotels"][0]["nearest_stop_km"] is not None


def test_choosing_a_flight_marks_it_and_cost_and_reservations_use_it(client: TestClient) -> None:
    session_id, _ = _saved(client)
    answer = _choose(client, session_id, kind="flight", option_id="flight_f2")
    assert answer.status_code == 200, answer.text
    data = answer.json()["plan"]
    assert [ticket["selected"] for ticket in data["tickets"]] == [False, True]
    flight_line = next(line for line in data["cost"]["lines"] if line["label"].startswith("Flights"))
    assert flight_line["label"] == "Flights · Air 2" and flight_line["amount"] == 350.0
    assert [row["name"] for row in data["reservations"] if row["name"].startswith("Flight")] == ["Flight · Air 2"]
    # Landing at 15:00 leaves no room for the first day's morning stops: said in a note, not rescheduled.
    assert any(note.startswith("The chosen flight (Air 2)") and "Temple 0" in note for note in data["data_quality"]["notes"])


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "item", "target_id": "nope", "option_id": "alt_udon1"},
        {"kind": "item", "target_id": "m1", "option_id": "alt_nope"},
        {"kind": "hotel", "option_id": "hotel_nope"},
        {"kind": "flight", "option_id": "flight_nope"},
    ],
)
def test_unknown_ids_are_404(client: TestClient, body: dict) -> None:
    session_id, plan = _saved(client)
    answer = _choose(client, session_id, **body)
    assert answer.status_code == 404, answer.text
    assert client.app.state.container.store.get_plan(session_id).version == plan.version


def test_a_bad_kind_is_422_and_another_session_is_404(client: TestClient) -> None:
    session_id, _ = _saved(client)
    assert _choose(client, session_id, kind="day", option_id="x").status_code == 422
    other = client.post(f"/api/journeys/{session_id}/plan/choose", json={"kind": "hotel", "option_id": "hotel_h3"},
                        headers={"X-Client-Token": "someone-else"})
    assert other.status_code == 404


def test_rooms_are_fetched_once_kept_on_the_plan_and_empty_without_a_source(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    session_id, _ = _saved(client)
    container = client.app.state.container
    # No SerpApi key in tests: nothing is fetched and the answer is an empty list.
    empty = client.get(f"/api/journeys/{session_id}/plan/hotels/hotel_h2/rooms", headers=GUEST)
    assert empty.status_code == 200 and empty.json() == {"rooms": []}

    calls: list[dict] = []

    async def fake_request_json(*_: Any, **kwargs: Any) -> dict:
        calls.append(kwargs["params"])
        return json.loads(json.dumps(DETAILS))

    monkeypatch.setattr(hotels_module, "request_json", fake_request_json)
    source = SerpApiHotelSource(container.settings.model_copy(update={"serpapi_key": "test-key"}))
    monkeypatch.setattr(container.registry, "hotels", [source])
    first = client.get(f"/api/journeys/{session_id}/plan/hotels/hotel_h2/rooms", headers=GUEST)
    assert first.status_code == 200, first.text
    assert [room["name"] for room in first.json()["rooms"]] == ["Standard Double", "Deluxe Twin"]
    assert calls[0]["property_token"] == "tok-2"
    stored = container.store.get_plan(session_id)
    assert [room.name for room in next(s for s in stored.hotel_options if s.id == "hotel_h2").room_types] == ["Standard Double", "Deluxe Twin"]
    second = client.get(f"/api/journeys/{session_id}/plan/hotels/hotel_h2/rooms", headers=GUEST)
    assert second.json() == first.json() and len(calls) == 1
    assert client.get(f"/api/journeys/{session_id}/plan/hotels/hotel_nope/rooms", headers=GUEST).status_code == 404


def test_a_plan_stored_before_the_options_loads_with_the_first_flight_selected() -> None:
    plan = _plan("session-old")
    raw = json.loads(plan.model_dump_json())
    raw.pop("hotel_options")
    for stay in raw["hotels"]:
        for key in ("amenities", "hotel_class", "property_token", "nearby", "room_types", "nearest_stop_km", "nearest_stop_name"):
            stay.pop(key)
    for ticket in raw["tickets"]:
        ticket.pop("selected")
    loaded = TripPlan.model_validate(raw)
    assert loaded.hotel_options == [] and loaded.hotels[0].room_types == []
    assert [ticket.selected for ticket in loaded.tickets] == [True, False]
