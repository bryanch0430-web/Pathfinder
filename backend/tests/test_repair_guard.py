"""A repair re-shapes a plan; it cannot change a fact of it.

The writer that repairs a plan returns the whole plan again. Whatever it does to the parts a
repair has no business with (prices, coordinates, sources, flags, weather, the data gaps, the
cost) the plan that comes back has the original's values there, and a repair that brings in a
stop, a hotel, a ticket or a URL of its own is refused.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any, Callable

import pytest

from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.validation import repair_plan, tidy_data_gaps, validate_plan
from app.config import Settings
from app.domain.errors import PlanInvalidError
from app.domain.models import DataGap, DateRange, EvalCase, FailureKind, TripPlan, WeatherDay, utcnow
from app.eval_efficiency import accepted_plan, case_context
from app.eval_metering import MeteredLLM
from app.infrastructure.observability import NoopTracer

ERRORS = ["day 1: an item has no source"]
EVIL = {"title": "Pay here", "url": "https://evil.example/pay", "source": "writer"}


def _plan(settings: Settings, days: int = 3) -> TripPlan:
    case = EvalCase(id="t", dataset="design", input="", destination="Kyoto", days=days)
    return asyncio.run(accepted_plan(settings, case_context(case, "s")))


def _payload(plan: TripPlan) -> dict[str, Any]:
    return json.loads(plan.model_dump_json())


def _repair(settings: Settings, plan: TripPlan, reply: dict[str, Any]) -> tuple[TripPlan | None, PlanInvalidError | None, MeteredLLM]:
    llm = MeteredLLM(replies={"plan.repair": json.dumps(reply)})
    gateway = LLMGateway(llm, PromptLibrary(settings), NoopTracer(), settings)
    try:
        return asyncio.run(repair_plan(plan, ERRORS, gateway, settings)), None, llm
    except PlanInvalidError as failure:
        return None, failure, llm


def _accepted(settings: Settings, plan: TripPlan, reply: dict[str, Any]) -> TripPlan:
    repaired, failure, llm = _repair(settings, plan, reply)
    assert repaired is not None, failure
    assert llm.purposes == ["plan.repair"]
    return repaired


def _refused(settings: Settings, plan: TripPlan, reply: dict[str, Any], reason: str) -> None:
    repaired, failure, llm = _repair(settings, plan, reply)
    assert repaired is None and failure is not None
    assert llm.purposes == ["plan.repair"] * settings.max_repairs
    assert reason in str(failure), str(failure)


# -- facts a repair cannot change ---------------------------------------------------------------


def test_a_repair_cannot_erase_the_gaps_or_rewrite_the_cost_and_prices(settings: Settings) -> None:
    plan = _plan(settings)
    plan.data_quality.gaps.append(DataGap(section="hotels", kind=FailureKind.TIMEOUT, message="hotel source timed out", retryable=True))
    reply = _payload(plan)
    reply["data_quality"]["gaps"] = []
    reply["cost"]["total"] = 1.0
    for hotel in reply["hotels"]:
        if hotel.get("price_per_night"):
            hotel["price_per_night"]["amount"] = 1.0
    for ticket in reply["tickets"]:
        if ticket.get("price"):
            ticket["price"]["amount"] = 1.0

    repaired = _accepted(settings, plan, reply)

    assert repaired.data_quality.gaps == plan.data_quality.gaps
    assert repaired.cost == plan.cost
    assert [hotel.price_per_night for hotel in repaired.hotels] == [hotel.price_per_night for hotel in plan.hotels]
    assert [ticket.price for ticket in repaired.tickets] == [ticket.price for ticket in plan.tickets]


def test_a_repair_cannot_move_a_stop_or_flip_its_flags(settings: Settings) -> None:
    plan = _plan(settings)
    first = plan.days[0].items[0]
    reply = _payload(plan)
    stop = reply["days"][0]["items"][0]
    stop["geo"] = {"lat": 35.68, "lng": 139.69}  # Tokyo
    stop["locked"] = not first.locked
    stop["confirmed"] = not first.confirmed
    stop["booking_url"] = ""

    repaired = _accepted(settings, plan, reply)

    kept = repaired.days[0].items[0]
    assert (kept.geo, kept.locked, kept.confirmed, kept.booking_url) == (first.geo, first.locked, first.confirmed, first.booking_url)


def test_a_repair_cannot_make_up_coordinates_for_a_stop_that_has_none(settings: Settings) -> None:
    plan = _plan(settings)
    plan.days[0].items[0].geo = None
    assert validate_plan(plan, settings) == [f"{plan.days[0].items[0].name}: attraction without coordinates"]
    reply = _payload(plan)
    reply["days"][0]["items"][0]["geo"] = {"lat": 0.0, "lng": 0.0}

    repaired, failure, llm = _repair(settings, plan, reply)

    # The made-up point is not kept, so the plan is still invalid and the repair fails.
    assert repaired is None and failure is not None
    assert "attraction without coordinates" in str(failure)


def test_a_repair_cannot_add_a_forecast_the_plan_did_not_have(settings: Settings) -> None:
    plan = _plan(settings)
    plan.days[1].weather = None
    plan.data_quality.gaps.append(DataGap(section="weather", kind=FailureKind.TIMEOUT, message="forecast timed out", retryable=True))
    reply = _payload(plan)
    reply["days"][1]["weather"] = WeatherDay(
        date=plan.days[1].date, summary="Sunny and warm", high_c=31.0, low_c=22.0, source="writer", fetched_at=utcnow()
    ).model_dump(mode="json")

    repaired = _accepted(settings, plan, reply)

    assert repaired.days[1].weather is None
    # The gap that says why is still there once the plan is tidied.
    assert [gap.section for gap in tidy_data_gaps(repaired).data_quality.gaps if gap.section == "weather"] == ["weather"]


def test_a_repair_cannot_change_a_forecast_it_was_given(settings: Settings) -> None:
    plan = _plan(settings)
    reply = _payload(plan)
    reply["days"][0]["weather"]["high_c"] = 45.0
    reply["days"][0]["weather"]["summary"] = "Hot"

    repaired = _accepted(settings, plan, reply)

    assert repaired.days[0].weather == plan.days[0].weather


def test_a_repair_that_adds_a_missing_day_brings_no_forecast_or_ascent_for_it(settings: Settings) -> None:
    plan = _plan(settings)
    extended = plan.model_copy(deep=True)
    extended.dates = DateRange(start=plan.dates.start, end=plan.dates.end + timedelta(days=1))
    assert validate_plan(extended, settings) == ["day count does not match the trip date range", "days are not continuous or ordered"]
    new_date = extended.dates.end
    reply = _payload(extended)
    reply["days"].append(
        {
            "date": new_date.isoformat(),
            "title": "A free day",
            "items": [],
            "note": "",
            "elevation_gain_m": 640.0,
            "weather": WeatherDay(
                date=new_date, summary="Sunny, 31C, no rain all day", high_c=31.0, low_c=22.0, source="writer", fetched_at=utcnow()
            ).model_dump(mode="json"),
        }
    )

    repaired = _accepted(settings, extended, reply)

    added = repaired.days[-1]
    assert added.date == new_date and added.title == "A free day", "the day the repair added is kept"
    assert added.weather is None and added.elevation_gain_m is None, "the writer cannot supply its forecast or ascent"
    assert [day.weather for day in repaired.days[:-1]] == [day.weather for day in plan.days]


@pytest.mark.parametrize("part", ["meals", "hotel", "ticket"])
def test_a_repair_that_empties_the_sources_of_a_row_keeps_them(settings: Settings, part: str) -> None:
    plan = _plan(settings)
    reply = _payload(plan)
    for day in reply["days"]:
        for item in day["items"]:
            if part == "meals" and item["type"] == "meal":
                item["source_refs"] = []
    if part == "hotel":
        reply["hotels"][0]["source_refs"] = []
    if part == "ticket":
        reply["tickets"][0]["source_refs"] = []

    repaired = _accepted(settings, plan, reply)

    def urls(rows: Any) -> list[list[str]]:
        return [[ref.url for ref in row.source_refs] for row in rows]

    assert urls(i for _, i in repaired.iter_items()) == urls(i for _, i in plan.iter_items())
    assert urls(repaired.hotels) == urls(plan.hotels) and urls(repaired.tickets) == urls(plan.tickets)
    if part == "meals":
        assert any(item.type.value == "meal" and item.source_refs for _, item in repaired.iter_items())


def test_a_repair_may_still_fix_the_times_and_dates(settings: Settings) -> None:
    plan = _plan(settings)
    reply = _payload(plan)
    reply["days"][0]["items"][0]["start"] = "08:30"
    reply["days"][0]["items"][0]["end"] = "09:15"
    reply["days"][0]["note"] = "A calmer start."
    reply["hotels"][0]["check_out"] = str(plan.dates.end)
    reply["summary"] = "A three day trip."

    repaired = _accepted(settings, plan, reply)

    stop = repaired.days[0].items[0]
    assert (stop.start, stop.end) == ("08:30", "09:15")
    assert repaired.days[0].note == "A calmer start." and repaired.summary == "A three day trip."
    assert repaired.hotels[0].check_out == plan.dates.end


# -- structure and URLs: refused outright -------------------------------------------------------


def _hotel_url(reply: dict[str, Any]) -> None:
    reply["hotels"][0]["booking_url"] = "https://evil.example/pay"


def _hotel_source(reply: dict[str, Any]) -> None:
    reply["hotels"][0]["source_refs"].append(EVIL)


def _ticket_source(reply: dict[str, Any]) -> None:
    reply["tickets"][0]["source_refs"] = [EVIL]


def _item_booking_url(reply: dict[str, Any]) -> None:
    reply["days"][0]["items"][0]["booking_url"] = "https://evil.example/book"


def _item_note(reply: dict[str, Any]) -> None:
    reply["days"][0]["items"][0]["notes"] = "Book now at https://evil.example/book"


def _item_source(reply: dict[str, Any]) -> None:
    reply["days"][0]["items"][0]["source_refs"].append(EVIL)


@pytest.mark.parametrize(
    "tamper", [_hotel_url, _hotel_source, _ticket_source, _item_booking_url, _item_note, _item_source]
)
def test_a_repair_with_a_url_of_its_own_is_refused(settings: Settings, tamper: Callable[[dict[str, Any]], None]) -> None:
    plan = _plan(settings)
    assert plan.hotels and plan.tickets
    reply = _payload(plan)
    tamper(reply)

    _refused(settings, plan, reply, "not in the original plan")


def test_a_repair_keeps_the_urls_the_plan_already_had(settings: Settings) -> None:
    plan = _plan(settings)
    plan.days[0].items[0].notes = "Tickets: https://example.com/tickets."

    repaired = _accepted(settings, plan, _payload(plan))

    assert repaired.days[0].items[0].notes == plan.days[0].items[0].notes


# -- same-named rows ----------------------------------------------------------------------------


def _with_a_repeated_stop(settings: Settings) -> TripPlan:
    plan = _plan(settings)
    plan.days[1].items[0].name = plan.days[0].items[0].name
    return plan


def test_dropping_one_of_two_stops_with_the_same_name_is_refused(settings: Settings) -> None:
    plan = _with_a_repeated_stop(settings)
    reply = _payload(plan)
    dropped = reply["days"][1]["items"].pop(0)

    _refused(settings, plan, reply, dropped["name"])


def test_a_copy_of_a_known_stop_under_a_new_id_is_refused(settings: Settings) -> None:
    plan = _plan(settings)
    reply = _payload(plan)
    copy = json.loads(json.dumps(reply["days"][0]["items"][0]))
    copy["id"] = "item_added_by_writer"
    copy["start"], copy["end"] = "22:00", "22:20"
    reply["days"][1]["items"].append(copy)

    _refused(settings, plan, reply, "not in the original plan")


def test_two_hotel_rows_with_one_name_each_keep_their_own_fetch_time(settings: Settings) -> None:
    plan = _plan(settings)
    first = plan.hotels[0]
    second = first.model_copy(deep=True)
    second.id = "hotel_second_stay"
    first.fetched_at = utcnow() - timedelta(days=3)
    second.fetched_at = utcnow() - timedelta(hours=2)
    plan.hotels = [first, second]
    reply = _payload(plan)
    for row in reply["hotels"]:
        row.pop("fetched_at")

    repaired = _accepted(settings, plan, reply)

    assert [hotel.fetched_at for hotel in repaired.hotels] == [first.fetched_at, second.fetched_at]
