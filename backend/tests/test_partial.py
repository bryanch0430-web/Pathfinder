"""The day preview reads a plan that is still being written, and never raises."""

from __future__ import annotations

from app.plan.partial import partial_days

COMPLETE = """{
  "summary": "A short break",
  "days": [
    {"date": "2027-03-12", "title": "Arrival", "stops": [{"ref": "a1"}, {"ref": "m2"}]},
    {"date": "2027-03-13", "title": "Harbour", "stops": [{"ref": "a3"}]}
  ]
}"""


def test_complete_days_are_read_in_order():
    days = partial_days(COMPLETE)
    assert [day["date"] for day in days] == ["2027-03-12", "2027-03-13"]
    assert days[0]["title"] == "Arrival"
    assert [stop["ref"] for stop in days[0]["stops"]] == ["a1", "m2"]


def test_a_day_still_being_written_is_shown_from_what_is_there():
    marker = '"stops": [{"ref": "a3"}'
    cut = COMPLETE.index(marker) + len(marker)
    days = partial_days(COMPLETE[:cut])
    assert [day["date"] for day in days] == ["2027-03-12", "2027-03-13"]
    assert [stop["ref"] for stop in days[1]["stops"]] == ["a3"]


def test_stop_times_are_kept_when_present():
    text = '{"days": [{"date": "2027-03-12", "stops": [{"ref": "a1", "start": "09:00", "end": "10:30"}]}]}'
    stop = partial_days(text)[0]["stops"][0]
    assert (stop["start"], stop["end"]) == ("09:00", "10:30")


def test_a_half_written_date_is_not_shown_as_a_date():
    assert partial_days('{"days": [{"date": "2027-03') == []


def test_prose_and_fences_around_the_object_are_ignored():
    fenced = "```json\n" + COMPLETE + "\n```"
    assert len(partial_days(fenced)) == 2


def test_braces_inside_a_title_do_not_split_the_day():
    text = '{"days": [{"date": "2027-03-12", "title": "A {brace} and a \\"quote\\"", "stops": [{"ref": "a1"}]}]}'
    days = partial_days(text)
    assert len(days) == 1
    assert [stop["ref"] for stop in days[0]["stops"]] == ["a1"]


def test_no_days_key_returns_nothing():
    assert partial_days('{"summary": "still thinking"}') == []
    assert partial_days("") == []
    assert partial_days("not json at all") == []


def test_days_after_the_cap_are_ignored():
    rows = ",".join(f'{{"date": "2027-03-{index + 1:02d}"}}' for index in range(40))
    assert len(partial_days('{"days": [' + rows + "]}")) == 31
