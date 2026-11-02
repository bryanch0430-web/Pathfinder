from datetime import date

from app.application.planner import Planner
from app.infrastructure.providers.flights import _candidate

OUTBOUND = {
    "departure_airport": {"id": "HKG", "time": "2027-01-31 22:45"},
    "arrival_airport": {"id": "LHR", "time": "2027-02-01 05:35"},
    "airline": "British Airways",
    "flight_number": "BA 32",
    "duration": 800,
}
INBOUND = {
    "departure_airport": {"id": "LHR", "time": "2027-02-05 18:20"},
    "arrival_airport": {"id": "HKG", "time": "2027-02-06 14:10"},
    "airline": "British Airways",
    "flight_number": "BA 31",
    "duration": 760,
}


def test_round_trip_candidate_keeps_the_inbound_leg() -> None:
    itinerary = {
        "flights": [OUTBOUND],
        "return_flights": [INBOUND],
        "price": 1200,
        "total_duration": 1560,
    }

    candidate = _candidate(itinerary, "HKG", "LHR", "HKD", date(2027, 1, 31))

    assert candidate is not None
    assert candidate.origin == "HKG" and candidate.destination == "LHR"
    assert candidate.return_origin == "LHR" and candidate.return_destination == "HKG"
    assert candidate.return_depart_at is not None and candidate.return_depart_at.hour == 18
    assert candidate.return_arrive_at is not None
    assert candidate.return_flight_number == "BA 31"
    assert candidate.return_duration_minutes == 760


def test_one_way_candidate_has_no_return_leg() -> None:
    candidate = _candidate(
        {"flights": [OUTBOUND], "price": 700}, "HKG", "LHR", "HKD", date(2027, 1, 31)
    )

    assert candidate is not None
    assert candidate.return_depart_at is None
    assert candidate.return_origin == ""


def test_build_tickets_carries_the_return_leg_into_the_plan() -> None:
    candidate = _candidate(
        {"flights": [OUTBOUND], "return_flights": [INBOUND], "price": 1200},
        "HKG",
        "LHR",
        "HKD",
        date(2027, 1, 31),
    )
    assert candidate is not None

    tickets = Planner._build_tickets([candidate.model_dump(mode="json")])

    assert len(tickets) == 1
    ticket = tickets[0]
    assert ticket.return_depart_at is not None
    assert ticket.return_flight_number == "BA 31"
    assert ticket.return_origin == "LHR"
    # The round-trip fare belongs to the option, not to each leg.
    assert ticket.price is not None and ticket.price.amount == 1200
