"""A weather question on a trip of several cities looks up the city of the day asked about, or every city."""

from __future__ import annotations

import asyncio
from datetime import date
from types import SimpleNamespace

from plan_samples import make_plan, make_two_city_plan

from app.application.asking import AskPath
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.models import GeoPoint, TripContext, WeatherDay
from app.infrastructure.observability import NoopTracer

KYOTO = GeoPoint(lat=35.01, lng=135.77)
OSAKA = GeoPoint(lat=34.69, lng=135.50)


class Weather:
    """Records every forecast it is asked for and answers a day of weather for each date."""

    name = "fake-weather"

    def __init__(self) -> None:
        self.asked: list[tuple[GeoPoint, date, date]] = []

    async def forecast(self, geo, start: date, end: date):
        self.asked.append((geo.geo, start, end))
        days = (end - start).days + 1
        return [
            WeatherDay(
                date=date.fromordinal(start.toordinal() + i),
                summary=f"at {geo.geo.lat}",
                high_c=12.0,
                low_c=5.0,
            )
            for i in range(days)
        ]


def _ask(plan, question: str):
    weather = Weather()
    registry = SimpleNamespace(weather=[weather], geocoder=None, routes=[], web=[])
    path = AskPath(gateway=None, registry=registry, settings=Settings(_env_file=None))
    toolbox = Toolbox(path="ask", tracer=NoopTracer(), store=None, request_id="t")
    context = TripContext(session_id="s", destination=plan.destination.name)
    name, result, _source = asyncio.run(path._one_tool(question, plan, context, toolbox))
    return weather, name, result


def _two_cities():
    plan = make_two_city_plan()
    plan.segments[0].destination.geo = KYOTO
    plan.segments[1].destination.geo = OSAKA
    plan.destination.geo = KYOTO
    return plan


def test_a_day_in_the_second_city_looks_up_the_second_city() -> None:
    plan = _two_cities()

    weather, name, result = _ask(
        plan, "What is the weather on day 5?"
    )  # 7 December, Osaka

    assert name == "weather"
    assert [(geo, start, end) for geo, start, end in weather.asked] == [
        (OSAKA, date(2026, 12, 7), date(2026, 12, 7))
    ]
    assert [row["date"] for row in result] == ["2026-12-07"]


def test_a_day_in_the_first_city_looks_up_the_first_city() -> None:
    plan = _two_cities()

    weather, _, _ = _ask(plan, "Will it rain on day 2?")  # 4 December, Kyoto

    assert [geo for geo, _, _ in weather.asked] == [KYOTO]


def test_the_day_the_two_cities_share_is_the_city_arrived_in() -> None:
    plan = _two_cities()

    weather, _, _ = _ask(
        plan, "What is the weather on day 3?"
    )  # 5 December: leave Kyoto, arrive Osaka

    assert [geo for geo, _, _ in weather.asked] == [OSAKA]


def test_a_question_that_names_no_day_covers_both_cities_each_over_its_own_days() -> (
    None
):
    plan = _two_cities()

    weather, name, result = _ask(plan, "What is the weather like on this trip?")

    assert name == "weather"
    assert weather.asked == [
        (KYOTO, date(2026, 12, 3), date(2026, 12, 4)),
        (OSAKA, date(2026, 12, 5), date(2026, 12, 7)),
    ]
    # Every day once, each row says which city it is for.
    assert [(row["date"], row["city"]) for row in result] == [
        ("2026-12-03", "Kyoto"),
        ("2026-12-04", "Kyoto"),
        ("2026-12-05", "Osaka"),
        ("2026-12-06", "Osaka"),
        ("2026-12-07", "Osaka"),
    ]


def test_a_one_city_plan_is_looked_up_exactly_as_before() -> None:
    plan = make_plan()
    plan.destination.geo = KYOTO

    weather, _name, result = _ask(plan, "What is the weather on day 2?")

    assert weather.asked == [(KYOTO, plan.days[1].date, plan.days[1].date)]
    assert all("city" not in row for row in result)

    weather, _, result = _ask(plan, "What is the weather like?")

    assert weather.asked == [(KYOTO, plan.dates.start, plan.dates.end)]
    assert all("city" not in row for row in result)


def test_a_city_with_no_known_place_says_the_forecast_is_unavailable() -> None:
    plan = make_two_city_plan()  # no coordinates on the cities

    weather, name, result = _ask(plan, "What is the weather on day 5?")

    assert weather.asked == []
    assert name == "weather" and "unavailable" in result
