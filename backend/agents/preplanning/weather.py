"""Weather agent: "fetches the forecast for the travel dates" (proposal §4)."""

from __future__ import annotations

from datetime import timedelta

from backend.agents.preplanning.base import (
    AgentRun,
    PreplanningAgent,
    failure_reason,
    oldest_fetch,
    source_of,
)
from backend.agents.prompts import WEATHER_TOOLS_SYSTEM, Purpose
from backend.schemas.agents import AgentResult, AgentTask, WeatherData
from backend.schemas.common import AgentName, SectionStatus
from backend.schemas.tools import (
    MAX_FORECAST_SPAN_DAYS,
    ForecastPayload,
    ForecastRequest,
    ToolOperation,
    ToolRequest,
)
from backend.schemas.trip_plan import DailyForecast, Disruption, DisruptionKind


class WeatherAgent(PreplanningAgent):
    name = AgentName.WEATHER
    tools_purpose = Purpose.WEATHER_TOOLS
    tools_system = WEATHER_TOOLS_SYSTEM
    expected_operations = frozenset({ToolOperation.FORECAST})

    def canonical_requests(self, task: AgentTask) -> list[ToolRequest]:
        ctx = task.context
        if not (ctx.destination and ctx.start_date and ctx.end_date):
            return []
        end = min(ctx.end_date, ctx.start_date + timedelta(days=MAX_FORECAST_SPAN_DAYS - 1))
        return [ForecastRequest(location=ctx.destination, start_date=ctx.start_date, end_date=end)]

    async def _run(self, task: AgentTask, run: AgentRun) -> AgentResult:
        outcomes = await self.propose_and_call(task, run)
        trip_dates = set(task.context.date_range())
        forecasts: dict[str, DailyForecast] = {}
        failures = []
        for outcome in outcomes:
            if not (outcome.ok and isinstance(outcome.payload, ForecastPayload)):
                failures.append(outcome)
                continue
            source = source_of(outcome)
            for day in outcome.payload.days:
                if day.date in trip_dates:
                    forecasts[day.date.isoformat()] = DailyForecast(
                        **day.model_dump(), source=source
                    )
        if not forecasts:
            reason = failure_reason(failures[0]) if failures else "no forecast returned"
            return self.unavailable(reason)

        ordered = [forecasts[k] for k in sorted(forecasts)]
        disruptions = [
            Disruption(
                kind=DisruptionKind.WEATHER_WARNING,
                detail=f"Weather warning {f.warning_signal} on {f.date.isoformat()}",
                date=f.date,
            )
            for f in ordered
            if f.warning_signal
        ]
        return AgentResult(
            agent=self.name,
            status=SectionStatus.OK,
            data=WeatherData(forecasts=ordered),
            fetched_at=oldest_fetch(outcomes),
            disruptions=disruptions,
            reason=None if len(ordered) == len(trip_dates) else "forecast covers part of the trip",
        )
