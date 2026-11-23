from __future__ import annotations

from datetime import date

from app.core.dates import add_days, day_range, finish_dates, minutes_between, overlaps, shift_time


def test_finish_dates_fills_end_from_days():
    start, end, days = finish_dates(start="2027-04-10", end=None, days=4)
    assert start == "2027-04-10"
    assert end == "2027-04-13"
    assert days == 4


def test_finish_dates_fills_days_from_range():
    start, end, days = finish_dates(start="2027-04-10", end="2027-04-12", days=None)
    assert days == 3


def test_finish_dates_does_not_invent_a_start():
    start, end, days = finish_dates(start=None, end=None, days=3)
    assert start is None and end is None and days == 3


def test_finish_dates_swaps_a_reversed_range():
    start, end, _ = finish_dates(start="2027-04-13", end="2027-04-10", days=None)
    assert start == "2027-04-10" and end == "2027-04-13"


def test_day_range_and_add_days():
    days = day_range(date(2027, 4, 10), date(2027, 4, 12))
    assert [day.isoformat() for day in days] == ["2027-04-10", "2027-04-11", "2027-04-12"]
    assert add_days(date(2027, 4, 10), 4) == date(2027, 4, 13)


def test_overlaps_does_not_wrap_midnight():
    assert overlaps("09:00", "10:00", "09:30", "10:30")
    assert not overlaps("09:00", "10:00", "10:00", "11:00")
    assert not overlaps("13:00", "14:00", "09:00", "10:00")


def test_minutes_and_shift():
    assert minutes_between("09:30", "11:00") == 90
    assert shift_time("23:30", 60) == "00:30"
