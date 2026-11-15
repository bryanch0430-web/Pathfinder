"""The plain weather answer on a trip of several cities names each day's city; a one-city answer is as before."""

from __future__ import annotations

from app.application.asking import _fallback_answer


def _row(day: int, city: str | None = None) -> dict:
    row = {
        "date": f"2026-12-{day:02d}",
        "status": "ok",
        "summary": "clear",
        "high_c": 12.0,
        "low_c": 5.0,
    }
    if city:
        row["city"] = city
    return row


def test_the_answer_names_the_city_of_each_day_and_tells_every_day() -> None:
    rows = [_row(n, "Kyoto" if n < 5 else "Osaka") for n in range(3, 8)]

    answer = _fallback_answer({"weather": rows}, "fake-weather")

    assert "2026-12-04 (Kyoto)" in answer and "2026-12-07 (Osaka)" in answer
    assert answer.count("clear") == 5


def test_the_answer_for_one_city_is_as_before() -> None:
    rows = [_row(n) for n in range(1, 7)]

    answer = _fallback_answer({"weather": rows}, "fake-weather")

    assert answer.count("clear") == 4
    assert answer == (
        "Forecast — 2026-12-01: clear, 12° / 5°; 2026-12-02: clear, 12° / 5°; "
        "2026-12-03: clear, 12° / 5°; 2026-12-04: clear, 12° / 5° (fake-weather)."
    )
