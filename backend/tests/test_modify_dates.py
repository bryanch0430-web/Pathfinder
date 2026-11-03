"""Editing a plan's dates: the new version, what the diff and the reply say, and what the edit keeps.

Offline: the sandbox providers. `_modify` (test_modify_targets) runs one edit on a Kyoto plan with the
context moved by hand; the turn tests run the whole `TurnService` on the metered rig with a store.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import pytest

from app.application.orchestrator import _describe_diff
from app.application.planner import Planner
from app.application.router import REPLAN_MESSAGE
from app.application.validation import diff_plans
from app.config import Settings
from app.domain.models import EvalCase, FailureKind, Intent, Money, RouteDecision, TripPlan, WeatherDay
from app.eval_efficiency import Rig, accepted_plan, build_rig, case_context
from app.eval_metering import RunLedger
from app.evaluation import SandboxHotels
from app.infrastructure.providers.fx import FxService
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

    async def turn(self, message: str, *, scope_item_id: str | None = None) -> list:
        return [
            event
            async for event in self.rig.turns.handle_turn(
                session_id=self.session_id, message=message, scope_item_id=scope_item_id
            )
        ]


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


# -- G2: the page's "generate again" reaches the edit ----------------------------------------------

PAGE_HOOK = Path(__file__).resolve().parents[2] / "frontend" / "src" / "lib" / "usePathfinder.ts"


def test_the_page_sends_the_message_the_backend_reads_as_a_request_to_move_the_trip() -> None:
    sent = PAGE_HOOK.read_text(encoding="utf-8")

    assert f'await send(plan ? "{REPLAN_MESSAGE}" : "Plan my trip.");' in sent


def test_the_edit_reads_the_page_message_as_a_request_for_the_dates_the_form_holds(settings: Settings) -> None:
    plan, updated, diff = _modify(settings, REPLAN_MESSAGE, [], new_range=WEEK_LATER)

    assert (updated.dates.start, updated.dates.end) == WEEK_LATER
    assert updated.version == plan.version + 1 and [change["kind"] for change in diff][:1] == ["dates"]
    assert not any("could not tell which stop" in gap.message for gap in updated.data_quality.gaps)


def test_saving_the_form_and_generating_again_moves_the_trip(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        trip.save_form(*WEEK_LATER)

        events = await trip.turn(REPLAN_MESSAGE)

        assert not [event for event in events if event.event == "error"]
        route = next(event.data for event in events if event.event == "route")
        assert (route["intent"], route["affected_parts"]) == ("modify", ["dates"])
        latest = trip.store.get_plan(trip.session_id)
        assert (latest.dates.start, latest.dates.end) == WEEK_LATER and latest.version == 2
        assert latest.trip_id == trip.plan.trip_id
        assert latest.hotels[0].name == trip.plan.hotels[0].name
        assert reply_of(events).startswith("Updated to version 2. The trip dates changed from ")
        assert "could not tell which stop" not in reply_of(events)

    run_trip(settings, scenario)


def test_a_model_that_reads_the_page_message_as_a_new_trip_does_not_start_one(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        async def new_trip(**_: Any) -> RouteDecision:
            return RouteDecision(intent=Intent.PLAN, confidence=0.95, reason_code="model_route")

        trip.rig.turns.router.route = new_trip
        trip.save_form(*WEEK_LATER)

        events = await trip.turn(REPLAN_MESSAGE)

        assert next(event.data for event in events if event.event == "route")["intent"] == "modify"
        latest = trip.store.get_plan(trip.session_id)
        assert latest.trip_id == trip.plan.trip_id and latest.version == 2
        assert (latest.dates.start, latest.dates.end) == WEEK_LATER

    run_trip(settings, scenario)


def test_generating_again_with_a_stop_selected_still_moves_the_trip(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        trip.save_form(*WEEK_LATER)
        selected = trip.plan.days[0].items[0].id

        events = await trip.turn(REPLAN_MESSAGE, scope_item_id=selected)

        latest = trip.store.get_plan(trip.session_id)
        assert (latest.dates.start, latest.dates.end) == WEEK_LATER and latest.version == 2
        assert [item.name for day in latest.days for item in day.items] == [
            item.name for day in trip.plan.days for item in day.items
        ]
        assert reply_of(events).startswith("Updated to version 2.")

    run_trip(settings, scenario)


def test_generating_again_with_the_form_unchanged_moves_nothing(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        events = await trip.turn(REPLAN_MESSAGE)

        assert not [event for event in events if event.event == "error"]
        latest = trip.store.get_plan(trip.session_id)
        assert (latest.dates.start, latest.dates.end) == (trip.plan.dates.start, trip.plan.dates.end)
        assert [row["version"] for row in trip.store.list_versions(trip.session_id)] == [1]

    run_trip(settings, scenario)


# -- G3, G4: the hotel the traveller has is kept, with its marks, when the dates or the length change ------


def accept_the_garden_inn(plan: TripPlan, *, confirmed: bool = True, locked: bool = True) -> None:
    """The traveller swapped to the sandbox's second hotel and accepted it (the planner picks the first)."""
    row = {
        "id": "h2",
        "name": "Kyoto Garden Inn",
        "area": "Old town",
        "nightly_price": {"amount": 90, "currency": "USD"},
        "price_kind": "reference",
        "source_refs": [],
    }
    plan.hotels = [Planner._build_hotel(row, plan.dates)]
    plan.hotels[0].confirmed, plan.hotels[0].locked = confirmed, locked


class _Quote(SandboxHotels):
    """The hotel source with another rate for the new dates (`factor`), or no rate at all."""

    def __init__(self, destination: str, *, factor: float = 1.0, unpriced: bool = False) -> None:
        super().__init__(destination)
        self.factor, self.unpriced = factor, unpriced

    async def search(self, **kwargs: Any):
        rows = await super().search(**kwargs)
        for row in rows:
            row.nightly_price = (
                None if self.unpriced else Money(amount=row.nightly_price.amount * self.factor, currency="USD")
            )
        return rows


def quote(**kwargs: Any) -> Callable[[Any], None]:
    return lambda registry: setattr(registry, "hotels", [_Quote("Kyoto", **kwargs)])


def test_a_change_of_length_keeps_the_hotel_the_traveller_accepted(settings: Settings) -> None:
    for message, days, new_days in (("make it 7 days", 5, 7), ("make it 4 days", 5, 4)):
        plan, updated, diff = _modify(
            settings, message, ["dates"], days=days, new_days=new_days, prepare=accept_the_garden_inn
        )

        assert len(updated.days) == new_days
        stay = updated.hotels[0]
        assert (stay.name, stay.confirmed, stay.locked) == ("Kyoto Garden Inn", True, True), message
        assert (stay.check_in, stay.check_out) == (updated.dates.start, updated.dates.end)
        assert stay.id == plan.hotels[0].id
        assert not any(change["kind"] == "hotel" for change in diff)
        nights = new_days - 1
        assert [line.label for line in updated.cost.lines][0] == f"Hotel · Kyoto Garden Inn × {nights} nights"
        assert "Overnight hotel selected" in {check.text for check in updated.constraints}
        assert next(check for check in updated.constraints if check.text == "Overnight hotel selected").evidence == (
            "Kyoto Garden Inn"
        )


def test_a_change_of_length_prices_the_kept_hotel_again(settings: Settings) -> None:
    _, updated, _ = _modify(
        settings,
        "make it 7 days",
        ["dates"],
        days=5,
        new_days=7,
        prepare=accept_the_garden_inn,
        break_registry=quote(factor=1.5),
    )

    assert updated.hotels[0].name == "Kyoto Garden Inn"
    assert updated.hotels[0].price_per_night.amount == 135


def test_a_kept_hotel_whose_new_price_is_missing_keeps_the_old_price_and_says_so(settings: Settings) -> None:
    for message, kwargs in (
        ("make it 7 days", {"days": 5, "new_days": 7}),
        ("move the trip a week later", {"new_range": WEEK_LATER}),
    ):
        plan, updated, _ = _modify(
            settings, message, ["dates"], prepare=accept_the_garden_inn, break_registry=quote(unpriced=True), **kwargs
        )

        stay = updated.hotels[0]
        assert (stay.name, stay.price_per_night.amount) == ("Kyoto Garden Inn", 90), message
        assert stay.fetched_at == plan.hotels[0].fetched_at, "the old figure keeps its own fetch time"
        nights = len(updated.days) - 1
        assert [line.amount for line in updated.cost.lines if line.label.startswith("Hotel")] == [
            round(FxService().convert(90, "USD", "JPY") * nights, 2)
        ], "the old rate still counts, for the new nights"
        assert any(
            gap.section == "hotels" and gap.message == "The hotel's price for the new dates could not be refreshed."
            for gap in updated.data_quality.gaps
        ), message


def test_a_date_shift_keeps_the_marks_of_the_hotel(settings: Settings) -> None:
    plan, updated, diff = _modify(
        settings,
        "move the trip a week later",
        ["dates"],
        new_range=WEEK_LATER,
        prepare=accept_the_garden_inn,
        break_registry=quote(factor=1.5),
    )

    stay = updated.hotels[0]
    assert (stay.confirmed, stay.locked) == (True, True)
    assert stay.id == plan.hotels[0].id
    assert stay.price_per_night.amount == 135, "priced again for the new dates"
    # The same hotel at a new rate for the new nights: never "the hotel changed".
    assert [change["kind"] for change in diff if change["kind"].startswith(("hotel", "stay"))] == [
        "stay",
        "hotel_price",
    ]


# -- G10: a request for a cheaper hotel while the dates move -------------------------------------


NO_CHEAPER = "No cheaper verified hotel was returned; the current hotel stays."
NOT_REFRESHED = "The hotel's price for the new dates could not be refreshed."


def _gap_texts(plan: TripPlan) -> list[str]:
    return [gap.message for gap in plan.data_quality.gaps if gap.section == "hotels"]


def test_a_request_for_a_cheaper_hotel_prices_the_current_one_for_the_new_dates(settings: Settings) -> None:
    """Nothing cheaper came back, so the hotel stays, at the rate quoted for the new dates and not the old one."""
    _, updated, _ = _modify(
        settings,
        "make it cheaper and move it to May",
        ["budget"],
        new_range=WEEK_LATER,
        prepare=lambda plan: accept_the_garden_inn(plan, confirmed=False, locked=False),
        break_registry=quote(factor=1.5),
    )

    stay = updated.hotels[0]
    assert (stay.name, stay.price_per_night.amount) == ("Kyoto Garden Inn", 135)
    assert (stay.check_in, stay.check_out) == WEEK_LATER
    assert [line.amount for line in updated.cost.lines if line.label.startswith("Hotel")] == [
        round(FxService().convert(135, "USD", "JPY") * 2, 2)
    ]
    assert _gap_texts(updated) == [NO_CHEAPER]


def test_a_request_for_a_cheaper_hotel_without_a_new_rate_keeps_the_old_rate_and_says_so(settings: Settings) -> None:
    _, updated, _ = _modify(
        settings,
        "make it cheaper and move it to May",
        ["budget"],
        new_range=WEEK_LATER,
        prepare=lambda plan: accept_the_garden_inn(plan, confirmed=False, locked=False),
        break_registry=quote(unpriced=True),
    )

    assert (updated.hotels[0].name, updated.hotels[0].price_per_night.amount) == ("Kyoto Garden Inn", 90)
    assert _gap_texts(updated) == [NOT_REFRESHED, NO_CHEAPER]


# -- G5: a hotel word in a message about dates is not a request for another hotel -------------------


class _WriterDraft:
    """A writer whose draft of the change names the parts it was told to."""

    available = True

    def __init__(self, parts: list[str]) -> None:
        self.parts = parts

    async def json(self, *_: Any, **__: Any) -> dict:
        return {"description": "Moved the trip.", "affected_parts": self.parts, "is_question": False, "confidence": 0.9}


def _edit(
    settings: Settings,
    message: str,
    *,
    route_parts: list[str] | None = None,
    form: tuple[date, date] | None = WEEK_LATER,
    writer: Any = None,
    prepare: Callable[[TripPlan], None] | None = None,
    scope_item_id: str | None = None,
):
    """One edit with the parts the real rule router names for the message, as `_modify` does but with a writer
    and a chip of your own. `form` is the date range the trip form holds (None: the form matches the plan)."""
    from app.application.modification import ModifyPath
    from app.application.router import affected_parts_for
    from app.application.toolbox import Toolbox
    from app.infrastructure.observability import NoopTracer
    from tests.test_modify_targets import _build

    gateway, planner, plan_path, context, plan = _build(settings)
    if prepare is not None:
        prepare(plan)
    if form is not None:
        context.start_date, context.end_date, context.days = form[0], form[1], (form[1] - form[0]).days + 1
    modify = ModifyPath(planner=planner, plan_path=plan_path, settings=settings, gateway=writer or gateway)
    updated, diff = asyncio.run(
        modify.run(
            plan=plan,
            context=context,
            message=message,
            route_parts=affected_parts_for(message) if route_parts is None else route_parts,
            toolbox=Toolbox(path="modify", tracer=NoopTracer(), store=None, request_id="t"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
            scope_item_id=scope_item_id,
        )
    )
    return plan, updated, diff, context


HOTEL_WORDS_IN_A_DATE_MESSAGE = [
    "keep the hotel but move to March",
    "extend my stay by two days",
    "move the dates but keep the same room",
    "same trip, same hotel, new dates",
    "stay at the same hotel but go on 20 March instead",
    "shift my stay by a day",
]


def test_a_hotel_word_in_a_message_about_dates_does_not_swap_the_hotel(settings: Settings) -> None:
    for message in HOTEL_WORDS_IN_A_DATE_MESSAGE:
        plan, updated, diff, _ = _edit(settings, message)

        assert updated.hotels[0].name == plan.hotels[0].name, message
        assert not any(change["kind"] == "hotel" for change in diff), message
        assert (updated.dates.start, updated.dates.end) == WEEK_LATER, message
        assert updated.version == plan.version + 1, message


def test_a_writer_that_adds_the_hotel_to_a_date_message_does_not_swap_it_either(settings: Settings) -> None:
    for parts in (["dates", "hotel"], ["hotel"]):
        writer = _WriterDraft(parts)
        plan, updated, diff, _ = _edit(settings, "move the trip a week later", route_parts=[], writer=writer)

        assert updated.hotels[0].name == plan.hotels[0].name, parts
        assert (updated.dates.start, updated.dates.end) == WEEK_LATER, parts


def test_keeping_the_hotel_is_never_a_swap_even_when_no_date_moves(settings: Settings) -> None:
    plan, updated, diff, _ = _edit(settings, "keep the hotel but move to March", form=None)

    assert updated.hotels[0].name == plan.hotels[0].name
    assert diff == [] and updated.version == plan.version


def test_a_hotel_swap_that_also_moves_the_dates_still_swaps(settings: Settings) -> None:
    for message in ("swap the hotel and move the trip a week later", "change my hotel and shift the trip by a week"):
        plan, updated, diff, _ = _edit(settings, message)

        assert updated.hotels[0].name != plan.hotels[0].name, message
        assert (updated.dates.start, updated.dates.end) == WEEK_LATER, message
        assert any(change["kind"] == "hotel" for change in diff), message


def test_which_hotel_words_ask_for_another_hotel() -> None:
    from app.application.modification import _asks_for_another_hotel
    from app.application.router import affected_parts_for

    asks = {
        "swap the hotel for the other option, keep everything else.": True,
        "Swap the hotel for the last two nights.": True,
        "swap the hotel and move the trip a week later": True,
        "change my hotel and shift the trip by a week": True,
        "move the trip a week later and switch to a ryokan": True,
        "I don't want to keep this hotel": True,
        "I want a different hotel, not the same hotel": True,
        "a quieter hotel please": True,
        "keep the hotel, swap Gion for something else": False,
        "don't keep the hotel, move the trip a week later": False,
        **{message: False for message in HOTEL_WORDS_IN_A_DATE_MESSAGE},
    }
    for message, expected in asks.items():
        assert _asks_for_another_hotel(message, affected_parts_for(message)) is expected, message


# -- G8: dates the form holds are not applied by a message that does not ask for them -----------------


def _rows(plan: TripPlan) -> list[tuple]:
    return [(item.id, item.name, item.start, item.end) for day in plan.days for item in day.items]


def test_an_edit_that_does_not_ask_for_other_dates_leaves_the_trip_where_it_is(settings: Settings) -> None:
    """The form holds other dates (it was saved, not generated); "add a temple on day 2" is not a request for them."""
    for message in ("add a temple on day 2", "remove Gion from day 3", "swap the hotel for the last two nights"):
        plan, updated, diff, context = _edit(settings, message)

        assert (updated.dates.start, updated.dates.end) == (plan.dates.start, plan.dates.end), message
        assert [day.date for day in updated.days] == [day.date for day in plan.days], message
        assert not any(change["kind"] in {"dates", "stay", "weather"} for change in diff), message
        assert [ticket.depart_at for ticket in updated.tickets] == [ticket.depart_at for ticket in plan.tickets]
        assert (context.start_date, context.end_date) == (plan.dates.start, plan.dates.end), (
            "the edit reads the plan's own dates, whatever the form holds"
        )


def test_the_dates_a_form_holds_wait_for_the_message_that_asks_for_them(settings: Settings) -> None:
    async def scenario(trip: Trip) -> None:
        trip.save_form(*WEEK_LATER)
        stop = trip.plan.days[1].items[0].name

        await trip.turn(f"move {stop} later in the afternoon")
        unrelated = await trip.turn("add a temple on day 2")

        assert not [event for event in unrelated if event.event == "error"]
        stored = trip.store.get_context(trip.session_id)
        assert (stored.start_date, stored.end_date) == WEEK_LATER, "the form keeps what the traveller saved"
        assert (trip.store.get_plan(trip.session_id).dates.start) == trip.plan.dates.start
        assert "dates" not in {change["kind"] for change in plan_event(unrelated)["diff"]}

        moved = await trip.turn(REPLAN_MESSAGE)

        assert (trip.store.get_plan(trip.session_id).dates.start) == WEEK_LATER[0]
        assert reply_of(moved).startswith("Updated to version ")

    run_trip(settings, scenario)


# -- G6: naming the hotel, or selecting a row, does not cancel a request for other dates ------------------


def test_naming_the_hotel_in_a_request_for_other_dates_still_moves_the_trip(settings: Settings) -> None:
    for message in (
        "keep The Kyoto Grand but move the trip a week later",
        "move the trip a week later, The Kyoto Grand is fine",
    ):
        plan, updated, diff, _ = _edit(settings, message)

        assert (updated.dates.start, updated.dates.end) == WEEK_LATER, message
        assert updated.hotels[0].name == plan.hotels[0].name == "The Kyoto Grand", message
        assert updated.version == plan.version + 1 and diff[0]["kind"] == "dates", message
        assert "Nothing in the plan matched" not in updated.change_summary


def test_a_selected_row_does_not_cancel_a_request_for_other_dates(settings: Settings) -> None:
    for chip in ("item_p1", "hotel_h1"):
        plan, updated, diff, _ = _edit(settings, "move the trip a week later", scope_item_id=chip)

        assert (updated.dates.start, updated.dates.end) == WEEK_LATER, chip
        assert [row[:2] for row in _rows(updated)] == [row[:2] for row in _rows(plan)], "no stop was replaced"
        assert diff[0]["kind"] == "dates", chip


def test_a_hotel_swap_while_the_form_holds_other_dates_changes_the_hotel_and_not_the_dates(
    settings: Settings,
) -> None:
    plan, updated, diff, _ = _edit(settings, "swap the hotel for another one")

    assert updated.hotels[0].name == "Kyoto Garden Inn"
    assert (updated.dates.start, updated.dates.end) == (plan.dates.start, plan.dates.end)
    assert [change["kind"] for change in diff] == ["hotel", "cost"]


# -- G16: a flight that does not leave on the new first day is not kept -----------------------------------


class _NoFlights:
    name = "no-flights"
    available = True

    async def search(self, **_: Any):
        raise TimeoutError("the flight source timed out")


def no_flights(registry: Any) -> None:
    registry.flights = [_NoFlights()]


def test_a_flight_of_the_old_dates_is_not_kept_when_no_new_flight_came_back(settings: Settings) -> None:
    for shift in (-1, 1, 7):
        new_range = (START + timedelta(days=shift), START + timedelta(days=shift + 2))
        plan, updated, _ = _modify(
            settings, "move the trip to other dates", ["dates"], new_range=new_range, break_registry=no_flights
        )

        assert plan.tickets, "the plan had a flight"
        assert updated.tickets == [], shift
        assert not [line for line in updated.cost.lines if line.label.startswith("Flights")], shift
        assert any(
            gap.section == "tickets" and "no longer fits the new dates" in gap.message
            for gap in updated.data_quality.gaps
        ), shift


def test_a_flight_that_came_back_for_the_new_dates_replaces_the_old_one(settings: Settings) -> None:
    new_range = (START - timedelta(days=1), START + timedelta(days=1))
    _, updated, _ = _modify(settings, "move the trip a day earlier", ["dates"], new_range=new_range)

    assert [ticket.depart_at.date() for ticket in updated.tickets] == [new_range[0]]
    assert not any("no longer fits the new dates" in gap.message for gap in updated.data_quality.gaps)


# -- G17: no hotel, so nothing to say about the current one -------------------------------------------------


def test_a_trip_without_a_hotel_is_not_told_that_its_current_hotel_stays(settings: Settings) -> None:
    one_day = (START + timedelta(days=7), START + timedelta(days=7))
    _, short, _ = _modify(settings, "move the trip a week later", ["dates"], days=1, new_range=one_day)
    _, longer, _ = _modify(
        settings,
        "move the trip a week later",
        ["dates"],
        new_range=WEEK_LATER,
        prepare=lambda plan: setattr(plan, "hotels", []),
        break_registry=lambda registry: setattr(registry, "hotels", []),
    )

    assert not [gap for gap in short.data_quality.gaps if "current hotel" in gap.message]
    assert [gap.message for gap in longer.data_quality.gaps if gap.section == "hotels"] == [
        "No verified hotel was returned for the new dates."
    ]


# -- G18: a forecast for some of the days ----------------------------------------------------------------


class _SomeDays:
    """A forecast source that answers for the first `rows` days only."""

    name = "some-days"
    available = True

    def __init__(self, rows: int) -> None:
        self.rows = rows

    async def forecast(self, geo: Any, start: date, end: date):
        return [
            WeatherDay(
                date=start + timedelta(days=offset), summary="Rain", high_c=15, low_c=8, source=self.name
            )
            for offset in range(self.rows)
        ]


def test_a_forecast_for_some_of_the_days_leaves_a_gap_for_the_others(settings: Settings) -> None:
    _, updated, _ = _modify(
        settings,
        "move the trip a week later",
        ["dates"],
        new_range=WEEK_LATER,
        break_registry=lambda registry: setattr(registry, "weather", [_SomeDays(1)]),
    )

    assert [day.weather is not None for day in updated.days] == [True, False, False]
    assert [gap.message for gap in updated.data_quality.gaps if gap.section == "weather"] == [
        "The forecast source answered for 1 of 3 days; the other 2 have no forecast."
    ]
    full = _modify(
        settings,
        "move the trip a week later",
        ["dates"],
        new_range=WEEK_LATER,
        break_registry=lambda registry: setattr(registry, "weather", [_SomeDays(3)]),
    )[1]
    assert not [gap for gap in full.data_quality.gaps if gap.section == "weather"]


# -- G19: the edges of the date range --------------------------------------------------------------------


def test_a_form_range_that_is_not_a_trip_is_refused_for_a_date_request_and_ignored_otherwise(
    settings: Settings,
) -> None:
    from app.domain.errors import PlanInvalidError

    broken = (START + timedelta(days=9), START + timedelta(days=6))
    with pytest.raises(PlanInvalidError) as refused:
        _modify(settings, "change the dates", ["dates"], new_range=broken)
    assert "dates" in str(refused.value)

    plan, updated, _ = _modify(settings, "swap the hotel for another one", ["hotel"], new_range=broken)
    assert (updated.dates.start, updated.dates.end) == (plan.dates.start, plan.dates.end)
    assert updated.hotels[0].name != plan.hotels[0].name


def test_a_move_to_dates_in_the_past_is_made_with_a_warning(settings: Settings) -> None:
    long_ago = (date.today() - timedelta(days=400), date.today() - timedelta(days=398))

    _, updated, diff = _modify(settings, "change the dates", ["dates"], new_range=long_ago)

    assert (updated.dates.start, updated.dates.end) == long_ago and diff[0]["kind"] == "dates"
    assert [gap.message for gap in updated.data_quality.gaps if gap.section == "dates"] == [
        "The new dates are in the past."
    ]
    # and the same when the trip also changes length
    longer = (long_ago[0], long_ago[0] + timedelta(days=4))
    _, rebuilt, _ = _modify(settings, "make it 5 days", ["dates"], new_range=longer)
    assert [gap.message for gap in rebuilt.data_quality.gaps if gap.section == "dates"] == [
        "The new dates are in the past."
    ]


# -- the whole page flow over HTTP ---------------------------------------------------------------------


def _events(body: str) -> list[tuple[str, dict]]:
    import json

    found = []
    for frame in body.split("\n\n"):
        name = next((line[6:].strip() for line in frame.splitlines() if line.startswith("event:")), None)
        data = next((line[5:].strip() for line in frame.splitlines() if line.startswith("data:")), None)
        if name and data:
            found.append((name, json.loads(data)))
    return found


def test_saving_other_dates_and_generating_again_over_http_moves_the_trip_and_keeps_the_old_version(
    settings: Settings,
) -> None:
    """What the page does: PATCH the form with other dates, then send its fixed message on the same trip."""
    from fastapi.testclient import TestClient

    from app.evaluation import SandboxRegistry
    from app.interface.api import create_app

    guest = {"X-Client-Token": "guest-dates"}
    settings = settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})
    with TestClient(create_app(settings)) as client:
        container = client.app.state.container
        container.plan_path.registry = SandboxRegistry(settings, "Kyoto")
        session_id = client.post("/api/journeys", json={"client_token": "guest-dates"}).json()["session"]["id"]
        form = {"destination": "Kyoto", "origin": "Hong Kong", "party_size": 2, "budget": 400000, "currency": "JPY"}
        client.patch(
            f"/api/journeys/{session_id}/state",
            json={**form, "start_date": START.isoformat(), "end_date": (START + timedelta(days=2)).isoformat()},
            headers=guest,
        )
        first = client.post(
            "/api/turn/stream", json={"session_id": session_id, "message": "Plan my trip", "locale": "en"}, headers=guest
        )
        assert [name for name, _ in _events(first.text) if name == "error"] == []
        assert client.get(f"/api/journeys/{session_id}", headers=guest).json()["plan"]["version"] == 1

        saved = client.patch(
            f"/api/journeys/{session_id}/state",
            json={"start_date": WEEK_LATER[0].isoformat(), "end_date": WEEK_LATER[1].isoformat()},
            headers=guest,
        )
        assert saved.status_code == 200
        again = client.post(
            "/api/turn/stream",
            json={"session_id": session_id, "message": REPLAN_MESSAGE, "locale": "en"},
            headers=guest,
        )

        events = _events(again.text)
        assert not [data for name, data in events if name == "error"]
        route = next(data for name, data in events if name == "route")
        assert (route["intent"], route["affected_parts"]) == ("modify", ["dates"])
        moved = next(data for name, data in events if name == "plan")
        assert moved["plan"]["version"] == 2 and moved["plan"]["dates"]["start"] == WEEK_LATER[0].isoformat()
        assert "dates" in [change["kind"] for change in moved["diff"]]
        opened = client.get(f"/api/journeys/{session_id}", headers=guest).json()
        assert sorted(row["version"] for row in opened["versions"]) == [1, 2]
        assert opened["plan"]["version"] == 2
        old = client.get(f"/api/journeys/{session_id}/itinerary", params={"version": 1}, headers=guest).json()["plan"]
        assert old["dates"]["start"] == START.isoformat()
        assert opened["context"]["start_date"] == WEEK_LATER[0].isoformat()
