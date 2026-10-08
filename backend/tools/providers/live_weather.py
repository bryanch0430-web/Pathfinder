"""Live weather provider — PROVISIONAL (team decision 5: vendors are provisional)."""

from __future__ import annotations

from backend.schemas.tools import ForecastDay, ForecastRequest


class LiveWeatherProvider:
    name = "live-weather"

    async def forecast(self, request: ForecastRequest) -> list[ForecastDay]:
        # TODO(provisional): weather vendor not chosen (e.g. Open-Meteo daily forecast, plus the
        # Hong Kong Observatory open-data API for official warning signals such as T8).
        raise NotImplementedError("TODO(provisional): live weather forecast is not implemented")
