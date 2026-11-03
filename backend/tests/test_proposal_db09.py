"""Proposal design behaviour DB-09 (p3 l.104-107, l.127-128).

An edit calls only the agents responsible for the change, keeps confirmed and
locked items, and the merged plan is checked before it is kept. Each test runs
one modify turn through the app's TurnService on the offline sandbox, and reads
which agents ran from the turn's agent events and which tools ran from the
turn's own tool log in the store.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass

from app.application.router import affected_parts_for
from app.application.validation import validate_plan
from app.config import Settings
from app.domain.models import EvalCase, GeoPoint, PlaceCandidate, TripPlan
from app.eval_efficiency import accepted_plan, build_rig, case_context
from app.eval_metering import RunLedger
from app.evaluation import _ref

DESTINATION = "Tokyo"
HOTEL, TICKET, ATTRACTION = "hotel", "ticket", "attraction"
CONFIRMED_STOP = "teamLab Planets"  # Day 1, third row
LOCKED_STOP = "Ghibli Museum"  # Day 2, last row


@dataclass
class Edit:
    before: TripPlan
    after: TripPlan
    diff: list[dict]
    agents: set[str]
    tools: Counter


def _protect(plan: TripPlan) -> None:
    for day in plan.days:
        for item in day.items:
            if item.name == CONFIRMED_STOP:
                item.confirmed = True
            if item.name == LOCKED_STOP:
                item.locked = True


def _rows(plan: TripPlan) -> dict[str, tuple]:
    return {
        item.id: (index, item.name, item.start, item.end, item.locked, item.confirmed)
        for index, day in enumerate(plan.days)
        for item in day.items
    }


def _edit(
    settings: Settings,
    message: str,
    *,
    extra_place: PlaceCandidate | None = None,
) -> Edit:
    """One modify turn on an accepted Tokyo plan with one confirmed and one locked stop."""
    settings = settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})
    ledger = RunLedger()
    try:
        rig = build_rig(settings, DESTINATION, ledger)
        if extra_place is not None:
            _add_place(rig.plan_path.registry, extra_place)
        session = ledger.store.create_session("guest-db09")
        context = case_context(EvalCase(id="db09", dataset="design", input="", destination=DESTINATION), session.id)
        ledger.store.save_context(context)
        before = asyncio.run(accepted_plan(settings, context))
        _protect(before)
        ledger.store.save_plan(before, "Accepted plan")
        rig.llm.route = {"intent": "modify", "confidence": 0.95, "affected_parts": affected_parts_for(message)}
        agents: set[str] = set()
        trace: dict = {}
        payload: dict = {}
        for event in asyncio.run(_events(rig, session.id, message)):
            assert event.event != "error", event.data
            if event.event == "agent" and event.data.get("phase") == "start":
                agents.add(event.data["agent"])
            elif event.event == "trace":
                trace = event.data
            elif event.event == "plan":
                payload = event.data
        tools = ledger.logged_tools(trace["request_id"])
        after = TripPlan.model_validate(payload["plan"])
    finally:
        ledger.close()
    return Edit(before, after, payload["diff"], agents, tools)


async def _events(rig, session_id: str, message: str) -> list:
    return [event async for event in rig.turns.handle_turn(session_id=session_id, message=message)]


def _add_place(registry, extra: PlaceCandidate) -> None:
    """The place source also lists one stop the accepted plan does not use."""
    source = registry.places[-1]
    inner = source.search

    async def search(**kwargs):
        rows = await inner(**kwargs)
        if kwargs.get("category") in {"restaurant", "meal", "food"}:
            return rows
        return [*rows, extra]

    source.search = search


def _named(plan: TripPlan, name: str):
    return next(((day, item) for day in plan.days for item in day.items if item.name == name), None)


def _assert_checked_and_kept(edit: Edit, settings: Settings, *, changed: set[str] = frozenset()) -> None:
    """The merged plan validates, and every confirmed or locked row is exactly where it was."""
    assert validate_plan(edit.after, settings) == []
    before, after = _rows(edit.before), _rows(edit.after)
    for item_id, row in before.items():
        if row[4] or row[5]:
            assert after.get(item_id) == row, (row, after.get(item_id))
    # Rows the edit did not name keep their place and their day.
    for item_id, row in before.items():
        if row[1] in changed or item_id not in after:
            continue
        assert after[item_id][:2] == row[:2]
    assert edit.after.trip_id == edit.before.trip_id


# ---------------------------------------------------------------------------
# Hotel swap: the hotel agent only
# ---------------------------------------------------------------------------


def test_db09_hotel_swap_calls_only_the_hotel_agent(settings: Settings) -> None:
    """Proposal DB-09: a hotel swap runs the hotel agent and its tool, and no stop moves."""
    edit = _edit(settings, "Swap the hotel for the other option, keep everything else.")

    assert edit.agents == {HOTEL}
    # FX converts the new nightly rate; no stop changed, so no transfer is fetched.
    assert edit.tools == Counter({"hotels.search": 1, "fx.rates": 1})
    assert edit.after.hotels[0].name != edit.before.hotels[0].name
    assert [ticket.model_dump() for ticket in edit.after.tickets] == [
        ticket.model_dump() for ticket in edit.before.tickets
    ]
    assert {change["kind"] for change in edit.diff} <= {"hotel", "cost"}
    _assert_checked_and_kept(edit, settings)
    assert _rows(edit.after) == _rows(edit.before)


def test_db09_hotel_swap_naming_nights_does_not_call_weather_or_flights(settings: Settings) -> None:
    """Proposal DB-09: "the last two nights" names the stay, not new dates; weather and flights stay."""
    edit = _edit(settings, "Swap the hotel for the last two nights.")

    assert edit.agents == {HOTEL}
    assert set(edit.tools) == {"hotels.search", "fx.rates"}
    assert not {"weather.forecast", "flights.search", "places.search"} & set(edit.tools)
    assert edit.after.hotels[0].name != edit.before.hotels[0].name
    assert [day.weather for day in edit.after.days] == [day.weather for day in edit.before.days]
    _assert_checked_and_kept(edit, settings)


# ---------------------------------------------------------------------------
# Flight change: the ticket agent only
# ---------------------------------------------------------------------------


def test_db09_flight_change_calls_only_the_ticket_agent(settings: Settings) -> None:
    """Proposal DB-09: a flight change runs the ticket agent and its tool; the hotel and stops stay."""
    edit = _edit(settings, "Change the flight to a later one.")

    assert edit.agents == {TICKET}
    assert edit.tools == Counter({"flights.search": 1, "fx.rates": 1})
    assert edit.after.hotels[0].name == edit.before.hotels[0].name
    _assert_checked_and_kept(edit, settings)
    assert _rows(edit.after) == _rows(edit.before)


def test_db09_flight_change_naming_a_day_does_not_swap_the_hotel(settings: Settings) -> None:
    """Proposal DB-09: "on the last day" is not a date change; the hotel agent must not run."""
    edit = _edit(settings, "Change the flight on the last day to a later one.")

    assert edit.agents == {TICKET}
    assert set(edit.tools) == {"flights.search", "fx.rates"}
    assert edit.after.hotels[0].name == edit.before.hotels[0].name
    assert not any(change["kind"] == "hotel" for change in edit.diff)
    _assert_checked_and_kept(edit, settings)
    assert _rows(edit.after) == _rows(edit.before)


# ---------------------------------------------------------------------------
# Remove a stop: no agent; only the transfer across the gap is fetched
# ---------------------------------------------------------------------------


def test_db09_removing_a_stop_calls_no_agent(settings: Settings) -> None:
    """Proposal DB-09: taking a stop out needs no new data; only the new neighbours' transfer is fetched."""
    edit = _edit(settings, "Remove Tsukiji Outer Market from day 1.")

    assert edit.agents == set()
    # The stops either side of the gap now meet: one logged transfer call for that day.
    assert edit.tools == Counter({"routes.segments": 1})
    assert _named(edit.after, "Tsukiji Outer Market") is None
    assert [change["kind"] for change in edit.diff if change["kind"] in {"added", "removed"}] == ["removed"]
    assert edit.after.hotels[0].name == edit.before.hotels[0].name
    _assert_checked_and_kept(edit, settings, changed={"Tsukiji Outer Market"})


def test_db09_a_confirmed_stop_is_not_removed(settings: Settings) -> None:
    """Proposal DB-09: a request to remove a confirmed stop leaves it, and still calls no agent."""
    edit = _edit(settings, f"Remove {CONFIRMED_STOP} from day 1.")

    assert edit.agents == set()
    assert set(edit.tools) <= {"routes.segments"}
    assert _named(edit.after, CONFIRMED_STOP) is not None
    _assert_checked_and_kept(edit, settings)


# ---------------------------------------------------------------------------
# Replace a stop: the attraction agent only
# ---------------------------------------------------------------------------


def test_db09_replacing_a_stop_calls_only_the_attraction_agent(settings: Settings) -> None:
    """Proposal DB-09: a replacement runs the attraction agent's tools and refreshes that day's transfers."""
    tower = PlaceCandidate(
        name="Tokyo Tower",
        category="Attraction",
        description="A verified stop in Tokyo, suitable for a 60-minute visit.",
        geo=GeoPoint(lat=35.6586, lng=139.7454),
        address="Tokyo",
        rating=4.5,
        review_count=900,
        opening_hours=["09:00-22:00"],
        source_refs=[_ref("Tokyo Tower")],
        tags=["tourist_attraction", "sandbox"],
    )
    edit = _edit(settings, "Replace Shibuya Sky with something else.", extra_place=tower)

    assert edit.agents == {ATTRACTION}
    assert set(edit.tools) == {"places.search", "places.notes", "routes.segments"}
    assert edit.tools["routes.segments"] == 1
    replaced = _named(edit.after, "Tokyo Tower")
    assert replaced is not None and _named(edit.after, "Shibuya Sky") is None
    assert edit.after.hotels[0].name == edit.before.hotels[0].name
    _assert_checked_and_kept(edit, settings, changed={"Shibuya Sky"})


# ---------------------------------------------------------------------------
# Change the pace: no agent; transfers of the thinned days only
# ---------------------------------------------------------------------------


def test_db09_changing_the_pace_calls_no_agent_and_keeps_protected_stops(settings: Settings) -> None:
    """Proposal DB-09: a pace change reshapes the days from the plan alone; confirmed and locked stops stay."""
    edit = _edit(settings, "Only one place a day, please.")

    assert edit.agents == set()
    assert set(edit.tools) == {"routes.segments"}
    # At most one logged transfer call per day.
    assert edit.tools["routes.segments"] <= len(edit.after.days)
    assert _named(edit.after, CONFIRMED_STOP) is not None
    assert _named(edit.after, LOCKED_STOP) is not None
    for day_before, day_after in zip(edit.before.days, edit.after.days):
        sights = [item for item in day_after.items if item.type.value == "attraction"]
        protected = [item for item in sights if item.locked or item.confirmed]
        # A protected stop takes the day's one place; otherwise the first stop stays.
        assert len(sights) - len(protected) == (0 if protected else 1), [item.name for item in sights]
        assert {item.name for item in day_after.items} <= {item.name for item in day_before.items}
    assert edit.after.hotels[0].name == edit.before.hotels[0].name
    _assert_checked_and_kept(
        edit,
        settings,
        changed={item.name for day in edit.before.days for item in day.items},
    )
