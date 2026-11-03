"""Editing a plan's dates: the new version, what the diff and the reply say, and what the edit keeps.

Offline: the sandbox providers. `_modify` (test_modify_targets) runs one edit on a Kyoto plan with the
context moved by hand; the turn tests run the whole `TurnService` on the metered rig with a store.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable

from app.application.orchestrator import _describe_diff
from app.application.validation import diff_plans
from app.config import Settings
from app.domain.models import EvalCase, TripPlan, WeatherDay
from app.eval_efficiency import Rig, accepted_plan, build_rig, case_context
from app.eval_metering import RunLedger
from tests.test_modify_targets import _modify

START = date.today() + timedelta(days=30)
WEEK_LATER = (START + timedelta(days=7), START + timedelta(days=9))


# -- a turn on a stored trip ---------------------------------------------------------------------


@dataclass
class Trip:
    ledger: RunLedger
    rig: Rig
    session_id: str
    plan: TripPlan

    @property
    def store(self):
        return self.ledger.store

    def save_form(self, start: date, end: date) -> None:
        """What the page's Save does: the trip form takes other dates, the plan keeps its own."""
        context = self.store.get_context(self.session_id)
        context.start_date, context.end_date, context.days = start, end, (end - start).days + 1
        self.store.save_context(context)

    async def turn(self, message: str, *, route: dict[str, Any] | None = None) -> list:
        if route is not None:
            self.rig.llm.route = route
        return [event async for event in self.rig.turns.handle_turn(session_id=self.session_id, message=message)]


def run_trip(
    settings: Settings,
    scenario: Callable[[Trip], Any],
    *,
    days: int = 3,
    prepare: Callable[[TripPlan], None] | None = None,
) -> None:
    """Run `scenario` on a stored trip with an accepted plan (version 1), offline."""
    settings = settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})

    async def main() -> None:
        ledger = RunLedger()
        try:
            rig = build_rig(settings, "Kyoto", ledger)
            session = ledger.store.create_session("guest-dates")
            case = EvalCase(id="dates", dataset="design", input="", destination="Kyoto", days=days)
            context = case_context(case, session.id)
            plan = await accepted_plan(settings, context)
            if prepare is not None:
                prepare(plan)
            ledger.store.save_context(context)
            ledger.store.save_plan(plan, "Accepted plan")
            await scenario(Trip(ledger, rig, session.id, plan))
        finally:
            ledger.close()

    asyncio.run(main())


def reply_of(events: list) -> str:
    return "".join(event.data["text"] for event in events if event.event == "token")


def plan_event(events: list) -> dict:
    return next(event.data for event in events if event.event == "plan")


# -- G1: a change of dates is a version of its own -----------------------------------------------


def test_a_change_of_dates_only_is_a_new_version_with_the_dates_in_the_diff(settings: Settings) -> None:
    plan, updated, diff = _modify(settings, "move the trip a week later", ["dates"], new_range=WEEK_LATER)

    assert (updated.dates.start, updated.dates.end) == WEEK_LATER
    assert updated.version == plan.version + 1
    moved = [change for change in diff if change["kind"] == "dates"]
    assert moved == [
        {
            "kind": "dates",
            "from": f"{plan.dates.start} – {plan.dates.end}",
            "to": f"{WEEK_LATER[0]} – {WEEK_LATER[1]}",
        }
    ]
    assert "Nothing in the plan matched" not in updated.change_summary


def test_the_reply_to_a_change_of_dates_says_what_moved(settings: Settings) -> None:
    plan, updated, diff = _modify(settings, "move the trip a week later", ["dates"], new_range=WEEK_LATER)

    text = _describe_diff(diff, updated)

    assert f"The trip dates changed from {plan.dates.start} – {plan.dates.end} to {WEEK_LATER[0]} – {WEEK_LATER[1]}." in text
    assert "No itinerary items needed to change" not in text
    # The stay follows the trip: the sentence about the trip says so, and the stay has none of its own.
    assert "The stay at" not in text


def test_a_moved_trip_keeps_the_version_it_left(settings: Settings) -> None:
    """The old dates stay as version 1; the move is version 2, and the reply names the new dates."""

    async def scenario(trip: Trip) -> None:
        trip.save_form(*WEEK_LATER)

        events = await trip.turn("move the trip a week later")

        assert not [event for event in events if event.event == "error"]
        assert sorted(row["version"] for row in trip.store.list_versions(trip.session_id)) == [1, 2]
        first, latest = trip.store.get_plan(trip.session_id, 1), trip.store.get_plan(trip.session_id)
        assert (first.dates.start, first.dates.end) == (trip.plan.dates.start, trip.plan.dates.end)
        assert (latest.dates.start, latest.dates.end) == WEEK_LATER and latest.version == 2
        data = plan_event(events)
        assert [change["kind"] for change in data["diff"] if change["kind"] == "dates"] == ["dates"]
        assert reply_of(events).startswith("Updated to version 2. The trip dates changed from ")
        assert "Everything else is unchanged" not in reply_of(events)

    run_trip(settings, scenario)


# -- the diff itself -----------------------------------------------------------------------------


def _kinds(old: TripPlan, new: TripPlan) -> list[str]:
    return [change["kind"] for change in diff_plans(old, new)]


def test_the_diff_sees_the_trip_range_and_the_date_of_each_day(settings: Settings) -> None:
    _, plan, _ = _modify(settings, "keep it", ["attraction"])
    later = plan.model_copy(deep=True)
    later.dates = later.dates.model_copy(
        update={"start": plan.dates.start + timedelta(days=7), "end": plan.dates.end + timedelta(days=7)}
    )
    for day in later.days:
        day.date += timedelta(days=7)

    assert _kinds(plan, later) == ["dates"]
    day_only = plan.model_copy(deep=True)
    day_only.days[1].date += timedelta(days=30)
    assert _kinds(plan, day_only) == ["dates"]


def test_the_diff_sees_the_nights_and_the_rate_of_the_same_hotel(settings: Settings) -> None:
    _, plan, _ = _modify(settings, "keep it", ["attraction"])
    other_nights = plan.model_copy(deep=True)
    other_nights.hotels[0].check_out -= timedelta(days=1)
    dearer = plan.model_copy(deep=True)
    dearer.hotels[0].price_per_night = dearer.hotels[0].price_per_night.model_copy(update={"amount": 150})

    assert _kinds(plan, other_nights) == ["stay"]
    changes = diff_plans(plan, dearer)
    assert [change["kind"] for change in changes] == ["hotel_price"]
    assert changes[0]["from"] == "USD 120" and changes[0]["to"] == "USD 150"
    assert "The nightly rate at The Kyoto Grand changed from USD 120 to USD 150." in _describe_diff(changes, dearer)


def test_the_diff_sees_a_forecast_that_changed_but_not_one_that_was_only_fetched_again(settings: Settings) -> None:
    _, plan, _ = _modify(settings, "keep it", ["attraction"])
    fetched_again = plan.model_copy(deep=True)
    fetched_again.days[0].weather = fetched_again.days[0].weather.model_copy(
        update={"fetched_at": fetched_again.days[0].weather.fetched_at + timedelta(hours=3)}
    )
    colder = plan.model_copy(deep=True)
    colder.days[0].weather = WeatherDay.model_validate(
        {**plan.days[0].weather.model_dump(mode="json"), "high_c": -3.0, "summary": "Snow"}
    )

    assert _kinds(plan, fetched_again) == []
    assert _kinds(plan, colder) == ["weather"]
    assert _describe_diff(diff_plans(plan, colder), colder) == "The forecast was updated."
