"""A desk session is live while the page is open, or for 24 hours after the last change."""

from datetime import datetime, timedelta, timezone

from app.application.desk_sessions import session_state

NOW = datetime(2026, 11, 18, 3, 0, tzinfo=timezone.utc)


def test_an_open_page_stays_live_after_a_day_of_quiet() -> None:
    state, on_page = session_state(NOW - timedelta(days=3), NOW - timedelta(seconds=5), NOW)
    assert state == "live"
    assert on_page is True


def test_a_closed_page_stays_live_for_twenty_four_hours() -> None:
    state, on_page = session_state(NOW - timedelta(hours=23), NOW - timedelta(hours=2), NOW)
    assert state == "live"
    assert on_page is False


def test_a_closed_page_older_than_a_day_is_a_completed_record() -> None:
    state, on_page = session_state(NOW - timedelta(hours=25), NOW - timedelta(hours=25), NOW)
    assert state == "completed"
    assert on_page is False
