"""The page's "Re-plan with the updated details." compares the trip form with the plan and does what the form asks.

Before, only other dates were applied. A changed destination, party size, budget or departure city was ignored with
"I could not tell which stop you meant" (the message named no stop), and a changed destination leaked into the edit:
the flight of a Kyoto plan was replaced by one to Tokyo. Now: another place is a new trip; another party size,
departure city or budget prices the hotel and the fares again and keeps the stay with its marks; nothing different says so.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from app.application.router import REPLAN_MESSAGE
from app.config import Settings
from app.domain.models import Money
from app.evaluation import SandboxHotels
from app.infrastructure.providers.fx import FxService
from tests.test_modify_dates import START, WEEK_LATER, Trip, accept_the_garden_inn, reply_of, run_trip

NOT_A_STOP = "could not tell which stop"
NOTHING_DIFFERS = "Nothing in the trip details differs from the plan, so nothing was changed."


def _fill(trip: Trip, **fields: Any) -> None:
    """What the page's Save does: the form takes other values, the plan keeps its own."""
    context = trip.store.get_context(trip.session_id)
    for name, value in fields.items():
        setattr(context, name, value)
    trip.store.save_context(context)


def _route(events: list) -> dict:
    return next(event.data for event in events if event.event == "route")


def test_another_destination_in_the_form_is_a_new_trip(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        _fill(trip, destination="Tokyo")

        events = await trip.turn(REPLAN_MESSAGE)

        assert not [event for event in events if event.event == "error"]
        assert (_route(events)["intent"], _route(events)["reason_code"]) == ("plan", "replan_destination")
        latest = trip.store.get_plan(trip.session_id)
        assert latest.destination.name == "Tokyo" and latest.trip_id != trip.plan.trip_id
        assert NOT_A_STOP not in reply_of(events)
        assert not [item for day in latest.days for item in day.items if item.locked or item.confirmed]

    run_trip(settings, scenario, prepare=accept_the_garden_inn)


def test_another_destination_and_other_dates_do_not_put_a_flight_to_the_new_city_on_the_old_plan(
    settings: Settings,
) -> None:
    async def scenario(trip: Trip) -> None:
        _fill(trip, destination="Tokyo", start_date=WEEK_LATER[0], end_date=WEEK_LATER[1], days=3)

        await trip.turn(REPLAN_MESSAGE)

        latest = trip.store.get_plan(trip.session_id)
        assert latest.destination.name == "Tokyo"
        assert latest.trip_id != trip.plan.trip_id, "a plan for Kyoto is not moved to Tokyo under its own id"

    run_trip(settings, scenario)


def test_another_party_size_prices_the_plan_again_and_keeps_the_stay_with_its_marks(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        _fill(trip, party_size=5)

        events = await trip.turn(REPLAN_MESSAGE)

        assert not [event for event in events if event.event == "error"]
        assert _route(events)["intent"] == "modify"
        latest = trip.store.get_plan(trip.session_id)
        assert latest.trip_id == trip.plan.trip_id and latest.version == 2
        assert (trip.plan.party_size, latest.party_size) == (2, 5)
        assert (latest.hotels[0].name, latest.hotels[0].confirmed, latest.hotels[0].locked) == (
            "Kyoto Garden Inn",
            True,
            True,
        )
        assert NOT_A_STOP not in reply_of(events)
        assert "5 travellers" in reply_of(events)
        data = next(event.data for event in events if event.event == "plan")
        assert {"kind": "party", "from": 2, "to": 5} in data["diff"]

    run_trip(settings, scenario, prepare=accept_the_garden_inn)


class _PartyHotels(SandboxHotels):
    """A hotel source whose quote is for the party it is asked about (the real one sends adults=party_size)."""

    def __init__(self, destination: str) -> None:
        super().__init__(destination)
        self.asked: list[int | None] = []

    async def search(self, **kwargs: Any):
        self.asked.append(kwargs.get("party_size"))
        rows = await super().search(**kwargs)
        for row in rows:
            row.nightly_price = Money(amount=row.nightly_price.amount * (kwargs.get("party_size") or 1), currency="USD")
        return rows


def _hotel_line(plan) -> float:
    return next(line.amount for line in plan.cost.lines if line.label.startswith("Hotel"))


def test_another_party_size_asks_the_hotel_source_again_and_quotes_the_stay_for_the_new_party(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        source = _PartyHotels("Kyoto")
        trip.rig.plan_path.registry.hotels = [source]
        _fill(trip, party_size=5)

        events = await trip.turn(REPLAN_MESSAGE)

        assert source.asked == [5]
        latest = trip.store.get_plan(trip.session_id)
        stay = latest.hotels[0]
        assert (stay.name, stay.confirmed, stay.locked, stay.id) == (
            "Kyoto Garden Inn",
            True,
            True,
            trip.plan.hotels[0].id,
        )
        assert (trip.plan.hotels[0].price_per_night.amount, stay.price_per_night.amount) == (90, 450)
        nights = len(latest.days) - 1
        assert _hotel_line(latest) == round(FxService().convert(450, "USD", latest.cost.currency) * nights, 2)
        assert "5 travellers" in reply_of(events)
        assert "The nightly rate at Kyoto Garden Inn changed from USD 90 to USD 450." in reply_of(events)

    run_trip(settings, scenario, prepare=accept_the_garden_inn)


def test_another_party_size_quotes_the_stay_the_planner_picked_too(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        source = _PartyHotels("Kyoto")
        trip.rig.plan_path.registry.hotels = [source]
        _fill(trip, party_size=5)

        await trip.turn(REPLAN_MESSAGE)

        latest = trip.store.get_plan(trip.session_id)
        assert source.asked == [5]
        assert (trip.plan.hotels[0].name, trip.plan.hotels[0].price_per_night.amount) == ("The Kyoto Grand", 120)
        assert (latest.hotels[0].name, latest.hotels[0].price_per_night.amount) == ("The Kyoto Grand", 600)

    run_trip(settings, scenario)


def test_a_party_size_whose_stay_price_does_not_come_back_keeps_the_old_rate_and_says_so(settings: Settings) -> None:
    class _Unpriced(_PartyHotels):
        async def search(self, **kwargs: Any):
            rows = await super().search(**kwargs)
            return [row for row in rows if row.name != "Kyoto Garden Inn"]

    async def scenario(trip: Trip) -> None:
        trip.rig.plan_path.registry.hotels = [_Unpriced("Kyoto")]
        _fill(trip, party_size=5)

        events = await trip.turn(REPLAN_MESSAGE)

        latest = trip.store.get_plan(trip.session_id)
        assert (latest.hotels[0].name, latest.hotels[0].price_per_night.amount) == ("Kyoto Garden Inn", 90)
        assert [gap.message for gap in latest.data_quality.gaps if gap.section == "hotels"] == [
            "No hotel price for the new trip details came back; the earlier rate is kept."
        ]
        assert "5 travellers" in reply_of(events)

    run_trip(settings, scenario, prepare=accept_the_garden_inn)


def test_another_departure_city_does_not_ask_the_hotel_source(settings: Settings) -> None:
    """The stay is quoted for who travels, not from where: only the fares are asked again."""

    async def scenario(trip: Trip) -> None:
        source = _PartyHotels("Kyoto")
        trip.rig.plan_path.registry.hotels = [source]
        _fill(trip, origin="Singapore")

        await trip.turn(REPLAN_MESSAGE)

        latest = trip.store.get_plan(trip.session_id)
        assert source.asked == []
        assert latest.hotels[0].price_per_night.amount == trip.plan.hotels[0].price_per_night.amount

    run_trip(settings, scenario)


def test_another_departure_city_is_applied_to_the_plan_and_the_fares(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        _fill(trip, origin="Singapore")

        events = await trip.turn(REPLAN_MESSAGE)

        latest = trip.store.get_plan(trip.session_id)
        assert (trip.plan.origin, latest.origin) == ("Hong Kong", "Singapore")
        assert latest.version == 2 and NOT_A_STOP not in reply_of(events)
        assert "Singapore" in reply_of(events)

    run_trip(settings, scenario)


def test_another_budget_is_applied_to_the_plan_and_the_cost(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        _fill(trip, budget=60000.0)

        events = await trip.turn(REPLAN_MESSAGE)

        latest = trip.store.get_plan(trip.session_id)
        assert (latest.budget.amount, latest.cost.budget) == (60000.0, 60000.0)
        assert latest.version == 2 and NOT_A_STOP not in reply_of(events)
        assert "budget" in reply_of(events)
        currency = trip.plan.budget.currency
        assert next(c for c in latest.constraints if c.text.startswith("Stay within")).text == f"Stay within {currency} 60,000"

    run_trip(settings, scenario)


def test_other_dates_and_another_party_size_are_both_applied(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        _fill(trip, party_size=4, start_date=WEEK_LATER[0], end_date=WEEK_LATER[1], days=3)

        events = await trip.turn(REPLAN_MESSAGE)

        latest = trip.store.get_plan(trip.session_id)
        assert (latest.dates.start, latest.dates.end) == WEEK_LATER and latest.party_size == 4
        assert latest.trip_id == trip.plan.trip_id and latest.version == 2
        assert "The trip dates changed from" in reply_of(events)

    run_trip(settings, scenario)


def test_nothing_different_in_the_form_is_said_and_asks_no_agent(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        events = await trip.turn(REPLAN_MESSAGE)

        assert reply_of(events) == NOTHING_DIFFERS
        assert [row["version"] for row in trip.store.list_versions(trip.session_id)] == [1]
        assert not [span for span in next(e.data for e in events if e.event == "trace")["spans"] if span["name"] == "modify.agents"]

    run_trip(settings, scenario)


def _spans_and_generations(events: list) -> tuple[list[str], list[str]]:
    trace = next(event.data for event in events if event.event == "trace")
    return [span["name"] for span in trace["spans"]], [row["purpose"] for row in trace["generations"]]


def test_nothing_different_in_the_form_spends_no_model_call_to_say_so(settings: Settings) -> None:
    """The page's own message needs no writer to be read when the form holds nothing the plan does not."""

    async def scenario(trip: Trip) -> None:
        events = await trip.turn(REPLAN_MESSAGE)

        spans, purposes = _spans_and_generations(events)
        assert reply_of(events) == NOTHING_DIFFERS
        assert "llm.modify.extract" not in spans and "modify.extract" not in purposes, (spans, purposes)

    run_trip(settings, scenario)


def test_a_form_that_differs_in_something_the_plan_does_not_keep_spends_no_model_call_either(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        _fill(trip, pace="relaxed", hard_constraints=["no early mornings"], hotel_style="luxury", preferences=["museums"])

        events = await trip.turn(REPLAN_MESSAGE)

        spans, purposes = _spans_and_generations(events)
        assert reply_of(events) == NOTHING_DIFFERS
        assert "llm.modify.extract" not in spans and "modify.extract" not in purposes, (spans, purposes)
        assert [row["version"] for row in trip.store.list_versions(trip.session_id)] == [1]

    run_trip(settings, scenario)


def test_a_form_that_differs_in_something_the_plan_keeps_still_reads_the_message_as_before(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        _fill(trip, party_size=3)

        events = await trip.turn(REPLAN_MESSAGE)

        assert "3 travellers" in reply_of(events)
        assert trip.store.get_plan(trip.session_id).party_size == 3

    run_trip(settings, scenario)


def test_a_form_that_differs_in_something_the_plan_does_not_keep_is_not_a_difference(settings: Settings) -> None:
    """Preferences, hotel style or language in the form are not details of the plan: nothing to re-plan."""

    async def scenario(trip: Trip) -> None:
        _fill(trip, hotel_style="luxury", preferences=["museums"])

        events = await trip.turn(REPLAN_MESSAGE)

        assert reply_of(events) == NOTHING_DIFFERS

    run_trip(settings, scenario)


def test_the_form_in_the_other_dates_flow_still_moves_the_trip(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        trip.save_form(START + timedelta(days=7), START + timedelta(days=9))

        events = await trip.turn(REPLAN_MESSAGE)

        latest = trip.store.get_plan(trip.session_id)
        assert latest.version == 2 and latest.trip_id == trip.plan.trip_id
        assert reply_of(events).startswith("Updated to version 2. The trip dates changed from ")

    run_trip(settings, scenario)
