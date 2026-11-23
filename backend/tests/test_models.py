from __future__ import annotations

from app.domain.models import (
    DayPlan,
    HotelStay,
    PlanItem,
    RoutePayload,
    TripContext,
    TripPlan,
)


def test_plan_defaults_are_complete():
    plan = TripPlan(trip_id="t1", session_id="s1")
    body = plan.model_dump(mode="json")
    for key in (
        "schema_version", "trip_id", "session_id", "version", "status", "destination",
        "origin", "dates", "party_size", "budget", "summary", "days", "hotels", "tickets",
        "cost", "constraints", "disruptions", "reservations", "data_quality",
        "change_summary", "created_at", "updated_at",
    ):
        assert key in body


def test_plan_item_defaults():
    item = PlanItem(id="i1", name="A temple")
    assert item.type == "attraction"
    assert item.price_kind == "none"
    assert item.locked is False
    assert item.source_refs == []


def test_route_payload_defaults():
    route = RoutePayload()
    assert route.intent == "unclear"
    assert route.threshold == 0.8
    assert route.affected_parts == []


def test_context_round_trip():
    context = TripContext(session_id="s1", destination="Kyoto", days=3, currency="JPY")
    again = TripContext(**context.model_dump())
    assert again.destination == "Kyoto"
    assert again.days == 3


def test_days_and_hotels_are_typed():
    day = DayPlan(date="2027-04-10", title="Arrival", items=[PlanItem(id="i1", name="Market")])
    hotel = HotelStay(id="h1", name="Hotel")
    assert day.items[0].name == "Market"
    assert hotel.price_per_night is None

def test_verifier_records_when_there_are_no_constraints():
    import asyncio

    from app.core.trace import TurnTrace
    from app.plan.validator import audit_constraints

    plan = TripPlan(trip_id="t1", session_id="s1")
    context = TripContext(session_id="s1")
    trace = TurnTrace(request_id="verify-empty")
    result = asyncio.run(audit_constraints(llm=None, plan=plan, context=context, trace=trace))
    assert result is None
    assert any("no hard constraints" in line for line in trace.why)
