"""Weather adapters. Forecast only: no source, no temperature.

Open-Meteo covers 16 days. Beyond that horizon the date is explicitly
`unavailable` with a reason instead of a fabricated number.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.config import Settings
from app.domain.errors import ProviderError, ProviderNoData
from app.domain.models import DataStatus, GeoLocation, WeatherDay, utcnow
from app.infrastructure.http import request_json

WEATHER_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    56: "Freezing drizzle",
    57: "Freezing drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    66: "Freezing rain",
    67: "Freezing rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    77: "Snow grains",
    80: "Light showers",
    81: "Showers",
    82: "Heavy showers",
    85: "Snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with hail",
    99: "Thunderstorm with heavy hail",
}


class OpenMeteoWeather:
    name = "open-meteo"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def forecast(self, geo: GeoLocation, start: date, end: date) -> list[WeatherDay]:
        today = date.today()
        horizon = today + timedelta(days=15)
        requested = [date.fromordinal(start.toordinal() + i) for i in range((end - start).days + 1)]
        if start > horizon:
            return [
                WeatherDay(
                    date=day,
                    status=DataStatus.UNAVAILABLE,
                    summary="Forecast not yet available",
                    source=self.name,
                    note="Open-Meteo publishes roughly 16 days ahead. This date is outside the window.",
                )
                for day in requested
            ]
        data = await request_json(
            "GET",
            "https://api.open-meteo.com/v1/forecast",
            source=self.name,
            params={
                "latitude": geo.geo.lat,
                "longitude": geo.geo.lng,
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "timezone": "auto",
                "start_date": start.isoformat(),
                "end_date": min(end, horizon).isoformat(),
            },
            timeout=self.settings.http_timeout_seconds,
            retries=self.settings.http_retries,
            user_agent=self.settings.user_agent,
        )
        daily = data.get("daily") or {}
        times = daily.get("time") or []
        if not times:
            raise ProviderError("Open-Meteo returned no daily data", source=self.name)
        by_date: dict[str, WeatherDay] = {}
        for index, raw_date in enumerate(times):
            code = _at(daily.get("weather_code"), index)
            try:
                day = date.fromisoformat(str(raw_date))
            except ValueError:
                continue
            by_date[day.isoformat()] = WeatherDay(
                date=day,
                status=DataStatus.OK,
                summary=WEATHER_CODES.get(int(code), "Mixed conditions") if code is not None else "Mixed conditions",
                high_c=_at(daily.get("temperature_2m_max"), index),
                low_c=_at(daily.get("temperature_2m_min"), index),
                precipitation_probability=_at(daily.get("precipitation_probability_max"), index),
                source=self.name,
                fetched_at=utcnow(),
            )
        out: list[WeatherDay] = []
        for day in requested:
            if day.isoformat() in by_date:
                out.append(by_date[day.isoformat()])
            else:
                out.append(
                    WeatherDay(
                        date=day,
                        status=DataStatus.UNAVAILABLE,
                        summary="No forecast",
                        source=self.name,
                        note="Outside the returned forecast window.",
                    )
                )
        return out


class OpenMeteoArchiveWeather:
    """Seasonal reference for dates beyond the 16-day forecast horizon.

    It averages the same calendar dates over the previous three years. The rows are
    explicitly `stale`, carry the archive note, and are never presented as a forecast.
    """

    name = "open-meteo-archive"

    def __init__(self, settings: Settings):
        self.settings = settings

    async def forecast(self, geo: GeoLocation, start: date, end: date) -> list[WeatherDay]:
        today = date.today()
        requested = [date.fromordinal(start.toordinal() + i) for i in range((end - start).days + 1)]
        samples: dict[date, list[tuple[float | None, float | None, int | None]]] = {
            day: [] for day in requested
        }
        # Three previous years, not a single anecdote.
        for year in (today.year - 1, today.year - 2, today.year - 3):
            try:
                win_start = start.replace(year=year)
                win_end = end.replace(year=year)
            except ValueError:  # 29 February
                continue
            try:
                data = await request_json(
                    "GET",
                    "https://archive-api.open-meteo.com/v1/archive",
                    source=self.name,
                    params={
                        "latitude": geo.geo.lat,
                        "longitude": geo.geo.lng,
                        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum",
                        "timezone": "auto",
                        "start_date": win_start.isoformat(),
                        "end_date": win_end.isoformat(),
                    },
                    timeout=self.settings.http_timeout_seconds,
                    retries=1,  # fixed: the archive is a fallback, not covered by HTTP_RETRIES
                    user_agent=self.settings.user_agent,
                )
            except Exception:
                continue
            daily = data.get("daily") or {}
            times = daily.get("time") or []
            for index, raw_date in enumerate(times):
                try:
                    day = date.fromisoformat(str(raw_date))
                except ValueError:
                    continue
                target = start + timedelta(days=(day - win_start).days)
                if target in samples:
                    code = _at(daily.get("weather_code"), index)
                    samples[target].append(
                        (
                            _at(daily.get("temperature_2m_max"), index),
                            _at(daily.get("temperature_2m_min"), index),
                            int(code) if code is not None else None,
                        )
                    )

        if not any(samples.values()):
            raise ProviderNoData("No historical weather reference was returned", source=self.name)

        out: list[WeatherDay] = []
        for day in requested:
            rows = [row for row in samples[day] if row[0] is not None and row[1] is not None]
            if not rows:
                out.append(
                    WeatherDay(
                        date=day,
                        status=DataStatus.UNAVAILABLE,
                        summary="No seasonal reference",
                        source=self.name,
                        note="No usable archive rows for these calendar dates.",
                    )
                )
                continue
            high = round(sum(row[0] for row in rows if row[0] is not None) / len(rows), 1)
            low = round(sum(row[1] for row in rows if row[1] is not None) / len(rows), 1)
            codes = [row[2] for row in rows if row[2] is not None]
            code = max(set(codes), key=codes.count) if codes else None
            out.append(
                WeatherDay(
                    date=day,
                    status=DataStatus.STALE,
                    summary=f"Seasonal reference · {WEATHER_CODES.get(int(code), 'mixed conditions')}",
                    high_c=high,
                    low_c=low,
                    precipitation_probability=None,
                    source=self.name,
                    note=f"Average of {len(rows)} previous years for this calendar date; not a forecast.",
                    fetched_at=utcnow(),
                )
            )
        return out


def _at(values: object, index: int) -> float | None:
    if not isinstance(values, list) or index >= len(values) or values[index] is None:
        return None
    return float(values[index])
