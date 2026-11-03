"""A forecast that failed leaves its gap while the dates carry only a seasonal reference.

The seasonal archive answering after the forecast failed fills every date, but the plan then holds
an average, not a forecast, and the traveller should be able to tell why. A gap about days that no
source covered goes once something fills them; a failure goes only when a live forecast does.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner
from app.application.planning import PlanPath
from app.application.toolbox import Toolbox
from app.application.validation import tidy_data_gaps
from app.config import Settings
from app.domain.errors import ProviderError
from app.domain.models import DataGap, DataStatus, EvalCase, FailureKind, TripPlan, WeatherDay
from app.eval_efficiency import accepted_plan, case_context, cost_sandbox
from app.infrastructure.observability import NoopTracer

TIMEOUT_GAP = DataGap(section="weather", kind=FailureKind.TIMEOUT, message="open-meteo: forecast timed out", retryable=True)
WINDOW_GAP = DataGap(section="weather", kind=FailureKind.NO_DATA, message="3 of 3 days are outside the 16-day forecast window.")


def _plan(settings: Settings, status: DataStatus, gaps: list[DataGap]) -> TripPlan:
    case = EvalCase(id="w", dataset="design", input="", destination="Kyoto", days=3)
    plan = asyncio.run(accepted_plan(settings, case_context(case, "s")))
    for day in plan.days:
        assert day.weather is not None
        day.weather.status = status
    plan.data_quality.gaps = [gap.model_copy() for gap in gaps]
    return plan


def _weather_gaps(plan: TripPlan) -> list[tuple[FailureKind, str]]:
    return [(gap.kind, gap.message) for gap in plan.data_quality.gaps if gap.section == "weather"]


def test_a_failed_forecast_keeps_its_gap_when_only_a_seasonal_reference_fills_the_dates(settings: Settings) -> None:
    plan = _plan(settings, DataStatus.STALE, [TIMEOUT_GAP])

    assert _weather_gaps(tidy_data_gaps(plan)) == [(FailureKind.TIMEOUT, TIMEOUT_GAP.message)]


def test_tidying_twice_changes_nothing(settings: Settings) -> None:
    plan = _plan(settings, DataStatus.STALE, [TIMEOUT_GAP])

    once = _weather_gaps(tidy_data_gaps(plan))

    assert _weather_gaps(tidy_data_gaps(plan)) == once


def test_a_gap_about_days_no_source_covered_goes_once_a_seasonal_reference_fills_them(settings: Settings) -> None:
    plan = _plan(settings, DataStatus.STALE, [WINDOW_GAP])

    assert _weather_gaps(tidy_data_gaps(plan)) == []


def test_a_failure_from_before_a_live_forecast_filled_the_dates_goes(settings: Settings) -> None:
    plan = _plan(settings, DataStatus.OK, [TIMEOUT_GAP, WINDOW_GAP])

    assert _weather_gaps(tidy_data_gaps(plan)) == []


def test_a_failure_stays_while_any_date_has_no_live_forecast(settings: Settings) -> None:
    plan = _plan(settings, DataStatus.OK, [TIMEOUT_GAP])
    plan.days[1].weather = plan.days[1].weather.model_copy(update={"status": DataStatus.STALE})

    assert _weather_gaps(tidy_data_gaps(plan)) == [(FailureKind.TIMEOUT, TIMEOUT_GAP.message)]


class _Forecast:
    available = True

    def __init__(self, name: str, error: BaseException | None = None, status: DataStatus = DataStatus.STALE) -> None:
        self.name = name
        self.error = error
        self.status = status

    async def forecast(self, *, geo: Any, start: date, end: date) -> list[WeatherDay]:
        if self.error is not None:
            raise self.error
        days = (end - start).days + 1
        return [
            WeatherDay(date=start + timedelta(days=n), status=self.status, summary="seasonal average", high_c=18.0, low_c=9.0, source=self.name)
            for n in range(days)
        ]


def test_the_plan_of_a_trip_whose_forecast_timed_out_says_so_though_the_archive_answered(
    settings: Settings, offline_gateway: LLMGateway
) -> None:
    registry = cost_sandbox(settings, "Kyoto")
    timeout = ProviderError("open-meteo timed out: ReadTimeout", kind=FailureKind.TIMEOUT, source="open-meteo")
    registry.weather = [_Forecast("open-meteo", timeout), _Forecast("open-meteo-archive")]
    context = case_context(EvalCase(id="w", dataset="design", input="", destination="Kyoto", days=3), "s")

    async def scenario() -> TripPlan:
        path = PlanPath(planner=Planner(offline_gateway, settings, registry.fx), registry=registry, settings=settings)
        return await path.run(
            context=context,
            session_id="s",
            toolbox=Toolbox(path="plan", tracer=NoopTracer(), store=None, request_id="req_weather"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )

    plan = asyncio.run(scenario())

    assert [day.weather.status for day in plan.days if day.weather] == [DataStatus.STALE] * 3
    gaps = [gap for gap in plan.data_quality.gaps if gap.section == "weather"]
    assert [(gap.kind, gap.retryable) for gap in gaps] == [(FailureKind.TIMEOUT, True)]
