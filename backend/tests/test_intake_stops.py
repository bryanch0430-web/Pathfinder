"""Cities read from shared material become the trip's stops, even when the form already holds a country."""

from __future__ import annotations

from datetime import date

from app.application.intake import _apply, _IntakeDraft
from app.config import Settings
from app.domain.models import TripContext, TripStop

CITIES = ["Tokyo", "Kanazawa", "Kyoto", "Osaka", "Hiroshima"]


def _draft(destination: str = "Japan", cities: list[str] = CITIES) -> _IntakeDraft:
    return _IntakeDraft(
        destination=destination,
        stops=[{"city": city, "nights": 2} for city in cities],
    )


def _run(draft: _IntakeDraft, context: TripContext, settings: Settings):
    result = _apply(
        draft,
        context,
        haystack="",
        sighted=True,
        today=date(2026, 11, 14),
        settings=settings,
    )
    return result


def test_cities_on_an_empty_form_become_stops_and_the_first_is_the_destination(
    settings: Settings,
) -> None:
    context = TripContext(session_id="s")

    result = _run(_draft(), context, settings)

    assert [stop.city for stop in context.stops] == CITIES
    assert context.destination == "Tokyo"
    assert "stops" in result.filled


def test_a_form_that_already_says_japan_gets_the_same_result(
    settings: Settings,
) -> None:
    context = TripContext(session_id="s", destination="Japan")

    result = _run(_draft(), context, settings)

    assert [stop.city for stop in context.stops] == CITIES
    assert context.destination == "Tokyo"
    assert "stops" in result.filled


def test_a_form_whose_destination_is_one_of_the_later_cities_keeps_the_order_given(
    settings: Settings,
) -> None:
    context = TripContext(session_id="s", destination="Kyoto")

    result = _run(_draft(destination="Kyoto"), context, settings)

    assert [stop.city for stop in context.stops] == CITIES
    assert context.destination == "Tokyo"
    assert "stops" in result.filled


def test_a_form_that_already_has_stops_is_left_alone(settings: Settings) -> None:
    mine = [TripStop(city="Osaka", nights=3), TripStop(city="Nara", nights=2)]
    context = TripContext(session_id="s", destination="Osaka", stops=list(mine))

    result = _run(_draft(), context, settings)

    assert context.stops == mine
    assert context.destination == "Osaka"
    assert "stops" not in result.filled


def test_one_city_stays_a_single_destination(settings: Settings) -> None:
    context = TripContext(session_id="s")

    result = _run(_draft(destination="Kyoto", cities=["Kyoto"]), context, settings)

    assert context.stops == []
    assert context.destination == "Kyoto"
    assert "stops" not in result.filled


def test_a_first_stop_that_is_the_destination_is_not_announced_as_a_change(
    settings: Settings,
) -> None:
    context = TripContext(session_id="s", destination="Tokyo")

    result = _run(_draft(destination="Tokyo"), context, settings)

    assert [stop.city for stop in context.stops] == CITIES
    assert "stops" not in result.filled
