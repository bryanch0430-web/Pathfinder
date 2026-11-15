"""The plan as an iCalendar (RFC 5545) file: stops, stays and the chosen transport, in each city's time zone."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from plan_samples import make_plan, make_two_city_plan, stop

from app.application.calendar_export import plan_calendar, timezone_block
from app.config import Settings
from app.domain.models import TripPlan
from app.interface.api import create_app


def _unfold(text: str) -> list[str]:
    assert text.endswith("\r\n")
    lines: list[str] = []
    for raw in text.split("\r\n")[:-1]:
        if raw.startswith(" "):
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _events(text: str) -> dict[str, dict[str, str]]:
    """Each VEVENT by its UID, as {"NAME;PARAMS": value}."""
    events: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            assert current is not None
            events[current["UID"]] = current
            current = None
        elif current is not None:
            name, _, value = line.partition(":")
            current[name] = value
    return events


def _uid(plan: TripPlan, item_id: str) -> str:
    return f"{item_id}.{plan.trip_id}@pathfinder"


def _tokyo_plan(days: int = 2) -> TripPlan:
    plan = make_plan(days)
    plan.destination.timezone = "Asia/Tokyo"
    plan.segments[0].destination.timezone = "Asia/Tokyo"
    return plan


def _two_cities() -> TripPlan:
    plan = make_two_city_plan()
    plan.destination.timezone = "Asia/Tokyo"
    plan.segments[0].destination.timezone = "Asia/Tokyo"
    # Not Osaka's real zone: a different one so the test can tell the cities apart.
    plan.segments[1].destination.timezone = "Asia/Seoul"
    return plan


def test_the_file_is_one_calendar_with_crlf_lines() -> None:
    text = plan_calendar(_tokyo_plan())
    lines = _unfold(text)
    assert lines[0] == "BEGIN:VCALENDAR"
    assert lines[-1] == "END:VCALENDAR"
    assert "VERSION:2.0" in lines
    assert any(line.startswith("PRODID:") for line in lines)
    assert "\n" not in text.replace("\r\n", "")


def test_a_stop_is_an_event_at_its_local_time_in_the_city_zone() -> None:
    plan = _tokyo_plan()
    text = plan_calendar(plan)
    event = _events(text)[_uid(plan, "item_d1_s1")]
    assert event["SUMMARY"] == "Temple 1.1"
    assert event["DTSTART;TZID=Asia/Tokyo"] == "20261203T090000"
    assert event["DTEND;TZID=Asia/Tokyo"] == "20261203T095000"
    assert "DTSTAMP" in event
    dinner = _events(text)[_uid(plan, "item_d2_dinner")]
    assert dinner["DTSTART;TZID=Asia/Tokyo"] == "20261204T190000"
    lines = _unfold(text)
    assert "BEGIN:VTIMEZONE" in lines
    assert "TZID:Asia/Tokyo" in lines
    assert "TZOFFSETTO:+0900" in lines


def test_a_city_without_a_zone_gets_floating_local_times_and_no_zone_block() -> None:
    plan = make_plan(2)
    text = plan_calendar(plan)
    event = _events(text)[_uid(plan, "item_d1_s1")]
    assert event["DTSTART"] == "20261203T090000"
    assert event["DTEND"] == "20261203T095000"
    assert "VTIMEZONE" not in text


def test_an_unknown_zone_name_falls_back_to_floating_time() -> None:
    plan = make_plan(2)
    plan.segments[0].destination.timezone = "Mars/Base_One"
    plan.destination.timezone = "Mars/Base_One"
    event = _events(plan_calendar(plan))[_uid(plan, "item_d1_s1")]
    assert event["DTSTART"] == "20261203T090000"


def test_a_stay_is_an_all_day_event_over_its_nights() -> None:
    plan = _tokyo_plan(3)
    event = _events(plan_calendar(plan))[_uid(plan, "hotel_1")]
    assert "Hotel Granvia" in event["SUMMARY"]
    assert event["DTSTART;VALUE=DATE"] == "20261203"
    assert event["DTEND;VALUE=DATE"] == "20261205"


def test_only_the_chosen_flight_is_an_event_with_each_end_in_its_own_zone() -> None:
    plan = _tokyo_plan()
    events = _events(plan_calendar(plan))
    flight = events[_uid(plan, "ticket_1")]
    assert "Cathay" in flight["SUMMARY"]
    # The origin has no zone in the plan: its end is floating, read in the traveller's own zone.
    assert flight["DTSTART"] == "20261203T091000"
    assert flight["DTEND;TZID=Asia/Tokyo"] == "20261203T135500"
    back = events[_uid(plan, "ticket_1-return")]
    assert back["DTSTART;TZID=Asia/Tokyo"] == "20261204T150000"
    assert back["DTEND"] == "20261204T182000"
    assert _uid(plan, "ticket_2") not in events
    assert _uid(plan, "ticket_2-return") not in events


def test_two_cities_use_each_city_zone_and_the_chosen_option_of_each_leg() -> None:
    plan = _two_cities()
    text = plan_calendar(plan)
    events = _events(text)
    assert (
        events[_uid(plan, "item_d1_s1")]["DTSTART;TZID=Asia/Tokyo"] == "20261203T090000"
    )
    assert (
        events[_uid(plan, "item_d4_s1")]["DTSTART;TZID=Asia/Seoul"] == "20261206T090000"
    )
    lines = _unfold(text)
    assert "TZID:Asia/Tokyo" in lines and "TZID:Asia/Seoul" in lines
    assert events[_uid(plan, "hotel_b")]["DTSTART;VALUE=DATE"] == "20261205"
    assert events[_uid(plan, "hotel_b")]["DTEND;VALUE=DATE"] == "20261207"
    assert _uid(plan, "t_out_1") in events and _uid(plan, "t_out_2") not in events
    # An estimate has no timetable: it is a day event on the leg's date, not a made-up time.
    train = events[_uid(plan, "t_ab_train")]
    assert train["DTSTART;VALUE=DATE"] == "20261205"
    assert train["DTEND;VALUE=DATE"] == "20261206"
    assert _uid(plan, "t_ab_bus") not in events
    home = events[_uid(plan, "t_home_1")]
    assert home["DTSTART;TZID=Asia/Seoul"] == "20261203T160000"
    assert home["DTEND"] == "20261203T180000"


def test_text_is_escaped_and_long_lines_are_folded_at_75_octets() -> None:
    plan = make_plan(1)
    awkward = "Café; Tea, Bar\\ " + "東京タワー" * 30
    plan.days[0].items[0] = stop(
        "item_d1_s1", awkward, "09:00", "09:50", address="1-2 Shiba;\nMinato, Tokyo"
    )
    text = plan_calendar(plan)
    for raw in text.split("\r\n"):
        assert len(raw.encode("utf-8")) <= 75
    event = _events(text)[_uid(plan, "item_d1_s1")]
    assert event["SUMMARY"] == "Café\\; Tea\\, Bar\\\\ " + "東京タワー" * 30
    assert event["LOCATION"] == "1-2 Shiba\\;\\nMinato\\, Tokyo"
    text.encode("utf-8").decode("utf-8")


def test_uids_are_stable_unique_and_tied_to_the_trip() -> None:
    plan = _two_cities()
    first = _events(plan_calendar(plan))
    again = _events(
        plan_calendar(TripPlan.model_validate(plan.model_dump(mode="json")))
    )
    assert first.keys() == again.keys()
    assert plan_calendar(plan) == plan_calendar(plan)
    other = plan.model_copy(update={"trip_id": "trip_other"})
    assert not set(first) & set(_events(plan_calendar(other)))


def test_no_links_are_written() -> None:
    text = plan_calendar(_two_cities()) + plan_calendar(_tokyo_plan())
    assert "http" not in text
    assert "URL" not in text


def test_a_zone_block_lists_a_daylight_saving_change_inside_the_trip() -> None:
    lines = timezone_block("America/New_York", date(2026, 10, 30), date(2026, 11, 3))
    assert lines[0] == "BEGIN:VTIMEZONE" and lines[-1] == "END:VTIMEZONE"
    assert "TZID:America/New_York" in lines
    assert "TZOFFSETTO:-0400" in lines
    assert "TZOFFSETTO:-0500" in lines
    assert "BEGIN:STANDARD" in lines and "BEGIN:DAYLIGHT" in lines


@pytest.fixture()
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


GUEST = {"X-Client-Token": "guest-calendar"}


def test_the_calendar_route_returns_the_latest_plan_as_an_ics_download(
    client: TestClient,
) -> None:
    session_id = client.post(
        "/api/journeys", json={"client_token": "guest-calendar"}
    ).json()["session"]["id"]
    missing = client.get(f"/api/journeys/{session_id}/calendar.ics", headers=GUEST)
    assert missing.status_code == 404
    plan = _two_cities()
    plan.session_id = session_id
    client.app.state.container.store.save_plan(plan, "test plan")
    response = client.get(f"/api/journeys/{session_id}/calendar.ics", headers=GUEST)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert "attachment" in response.headers["content-disposition"]
    assert ".ics" in response.headers["content-disposition"]
    assert _uid(plan, "item_d1_s1") in _events(response.text)
