from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

import app.services.enrichment as enrichment
import app.services.seasonality as seasonality
from app.core.config import settings

KYOTO = {"lat": "35.0115754", "lon": "135.7681441", "address": {"country_code": "jp"}}
TODAY = date.today()


def reply(payload, status=200):
    response = Mock()
    response.status_code = status
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def met_series(start: date, days: int, rain_per_hour=0.0, symbol="clearsky_day"):
    """Hourly steps (UTC) with next_1_hours, like the first ~2.5 days of a real forecast."""
    steps, midnight = [], datetime.combine(start, datetime.min.time())
    for hour in range(days * 24):
        steps.append({"time": (midnight + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%M:%SZ"), "data": {
            "instant": {"details": {"air_temperature": 10 + hour % 24 / 2}},
            "next_1_hours": {"summary": {"symbol_code": symbol}, "details": {"precipitation_amount": rain_per_hour}},
            "next_6_hours": {"summary": {"symbol_code": symbol}, "details": {"precipitation_amount": rain_per_hour * 6}},
        }})
    return steps


def climate_reply():
    months = seasonality.MONTHS
    return reply({"properties": {"parameter": {"T2M": {m: 4.0 if m == "JAN" else 22.0 for m in months},
                                               "PRECTOTCORR": {m: 6.2 if m == "JUN" else 2.0 for m in months}}}})


def router(holiday_on: date | None = None, rain_per_hour=0.0, fail=()):
    """Fake httpx.AsyncClient.get that answers each source by URL."""
    calls, real_get = [], httpx.AsyncClient.get

    async def get(self, url, params=None, **kwargs):
        name = next((key for key in ("nominatim", "met.no", "nager", "frankfurter", "power") if key in str(url)), None)
        if name is None:  # the test client calling the app itself
            return await real_get(self, url, params=params, **kwargs)
        calls.append(name)
        if name in fail:
            raise httpx.ConnectTimeout("down")
        if name == "nominatim":
            return reply([KYOTO])
        if name == "met.no":
            return reply({"properties": {"timeseries": met_series(TODAY, 4, rain_per_hour)}})
        if name == "nager":
            return reply([{"date": holiday_on.isoformat(), "localName": "体育の日", "name": "Sports Day",
                           "countryCode": "JP", "counties": None, "types": ["Public"]}] if holiday_on else [])
        if name == "frankfurter":
            return reply({"amount": 1.0, "base": params["base"], "date": TODAY.isoformat(),
                          "rates": {"INR": 0.61}})
        return climate_reply()

    return get, calls


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(enrichment, "CACHE_DB_PATH", tmp_path / "cache.sqlite3")
    monkeypatch.setattr(seasonality, "CACHE_DB_PATH", tmp_path / "cache.sqlite3")
    monkeypatch.setattr(enrichment, "NOMINATIM_INTERVAL", 0)
    monkeypatch.setattr(settings, "ENRICHMENT_ENABLED", True)


async def run(days, start=TODAY, currency="JPY"):
    async with httpx.AsyncClient() as client:
        return await enrichment.enrich(client, "Kyoto", start, days, currency)


def test_daily_forecast_groups_by_local_day_without_double_counting_rain():
    days = enrichment.daily_forecast(met_series(TODAY, 2, rain_per_hour=0.5, symbol="lightrain"), lon=135.77)
    # UTC+9: the first UTC day starts at 09:00 local, and a trailing sliver of the next day is kept (9 steps).
    first = days[TODAY]
    assert first["rain_mm"] == pytest.approx(0.5 * 15)  # 15 local hours on day one, next_1_hours only
    assert first["condition"] == "rain" and first["temp_min"] <= first["temp_mean"] <= first["temp_max"]


def test_condition_mapping_and_worst_daytime_sky():
    assert [enrichment._condition(s) for s in ("clearsky_day", "fair_night", "partlycloudy_day", "cloudy", "fog",
                                                "lightrainshowers_day", "heavysleet", "rainandthunder")] == \
        ["sun", "sun", "partly", "cloud", "fog", "rain", "snow", "storm"]


def test_forecast_tips_warn_about_rain_with_an_indoor_backup():
    places = [("Arashiyama bamboo grove", "Arashiyama bamboo grove sight"), ("Kyoto National Museum", "Kyoto National Museum museum")]
    tips = enrichment.forecast_tips({"rain_mm": 12.0, "condition": "rain", "temp_min": 18, "temp_max": 24}, places)
    assert tips["badge"] == {"tone": "warn", "text": "Rain"}
    assert "indoor backup for Arashiyama bamboo grove, such as Kyoto National Museum" in tips["notes"][0]["text"]
    dry = enrichment.forecast_tips({"rain_mm": 0.0, "condition": "sun", "temp_min": 15, "temp_max": 25}, places)
    assert dry == {"badge": {"tone": "good", "text": "Dry"}, "notes": []}


@pytest.mark.asyncio
async def test_forecast_inside_the_window_typical_beyond_it_plus_holidays_and_rates():
    days = [(1, "Kyoto", [("Fushimi Inari", "Fushimi Inari sight")]), (2, "Kyoto", []), (12, "Kyoto", [])]
    get, calls = router(holiday_on=TODAY + timedelta(days=1), rain_per_hour=0.5)
    with patch("httpx.AsyncClient.get", get):
        result = await run(days)

    by_day = {w.day: w for w in result.weather}
    assert by_day[1].kind == "forecast" and by_day[1].label == "Forecast" and by_day[1].rain_mm > 5
    assert by_day[1].badge.text == "Rain"
    far = by_day[12]
    assert far.kind == "typical" and far.label.startswith("Typical for ") and far.temp_min is None
    assert [(h.day, h.name, h.country) for h in result.holidays] == [(2, "Sports Day", "JP")]
    assert result.exchange.base == "JPY" and result.exchange.rates == {"INR": 0.61}
    assert {s.covers for s in result.sources} == {"Place lookup", "Weather forecast", "Typical weather",
                                                  "Public holidays", "Exchange rates"}
    assert calls.count("nominatim") == 1 and calls.count("met.no") == 1  # one base, looked up once
    assert [(p.name, round(p.lat, 2), p.country) for p in result.places] == [("Kyoto", 35.01, "jp")]


@pytest.mark.asyncio
async def test_each_source_is_called_once_per_cache_window():
    days = [(1, "Kyoto", []), (2, "Kyoto", [])]
    get, _ = router()
    with patch("httpx.AsyncClient.get", get):
        first = await run(days)
    with patch("httpx.AsyncClient.get", AsyncMock(side_effect=AssertionError("network used"))):
        second = await run(days)
    assert second == first


@pytest.mark.asyncio
async def test_a_failing_source_only_hides_its_own_part_and_is_retried_later():
    days = [(1, "Kyoto", [])]
    get, _ = router(fail=("met.no", "nager"))
    with patch("httpx.AsyncClient.get", get):
        result = await run(days)
    # No forecast, so the day falls back to the month's typical weather; holidays are simply missing.
    assert [w.kind for w in result.weather] == ["typical"] and result.holidays == []
    assert result.exchange is not None and "Weather forecast" not in {s.covers for s in result.sources}

    get, calls = router()
    with patch("httpx.AsyncClient.get", get):
        again = await run(days)
    assert "met.no" in calls and "nager" in calls  # failures were not cached
    assert [w.kind for w in again.weather] == ["forecast"]


@pytest.mark.asyncio
async def test_no_dates_means_only_the_exchange_rate_and_same_currency_means_none():
    get, calls = router()
    with patch("httpx.AsyncClient.get", get):
        undated = await run([(1, "Kyoto", [])], start=None)
        rupees = await run([(1, "Kyoto", [])], start=None, currency="INR")
    assert undated.weather == [] and undated.holidays == [] and undated.exchange.rates == {"INR": 0.61}
    assert rupees.exchange is not None  # INR trip in Japan still gets the yen rate
    assert "met.no" not in calls and "nager" not in calls

    requested = []
    async def spy(self, url, params=None, **kwargs):
        if "frankfurter" in str(url):
            requested.append(params["symbols"])
        return await get(self, url, params=params, **kwargs)
    with patch("httpx.AsyncClient.get", spy):
        async with httpx.AsyncClient() as client:
            await enrichment.enrich(client, "Kyoto", None, [(1, "Kyoto", [])], "JPY", home="USD")
    assert requested == ["USD"]  # the traveler's home currency replaces the INR default; JPY is the trip's own


@pytest.mark.asyncio
async def test_get_enrichment_is_off_switchable_and_swallows_everything(monkeypatch):
    days = [(1, "Kyoto", [])]
    monkeypatch.setattr(settings, "ENRICHMENT_ENABLED", False)
    assert await enrichment.get_enrichment("Kyoto", TODAY, days, "JPY") is None
    monkeypatch.setattr(settings, "ENRICHMENT_ENABLED", True)
    with patch.object(enrichment, "enrich", AsyncMock(side_effect=RuntimeError("boom"))):
        assert await enrichment.get_enrichment("Kyoto", TODAY, days, "JPY") is None


@pytest.mark.asyncio
async def test_document_endpoint_carries_conditions_even_before_a_plan_exists(client):
    headers = {"X-Guest-ID": "5b0c1c9e-0000-4000-8000-00000000e001"}
    trip_id = (await client.post("/api/trips", headers=headers)).json()["trip_id"]
    brief = {"action": "SUBMIT_TRIP_ONBOARDING", "origin": "Delhi", "destination": "Kyoto", "duration_days": 2,
             "planning_preferences": {"start_date": (TODAY + timedelta(days=1)).isoformat()},
             "budget_amount": 150000, "currency": "JPY"}
    await client.post(f"/api/trips/{trip_id}/messages", headers=headers, json={"message_type": "UI_ACTION", "payload": brief})
    get, _ = router(holiday_on=TODAY + timedelta(days=2))
    with patch("httpx.AsyncClient.get", get):
        doc = (await client.get(f"/api/trips/{trip_id}/document", headers=headers)).json()
    assert doc["mode"] == "none"
    conditions = doc["enrichment"]
    assert [(w["day"], w["kind"]) for w in conditions["weather"]] == [(1, "forecast"), (2, "forecast")]
    assert conditions["holidays"][0]["day"] == 2 and conditions["exchange"]["base"] == "JPY"


@pytest.mark.asyncio
async def test_agent_turn_facts_include_the_current_days_conditions():
    from app.agents.trip_planner.copilot.graph import _conditions
    copilot = {"destination": "Kyoto", "currency": "JPY", "current_day": 2, "days": [
        {"day": 1, "base": "Kyoto", "items": []},
        {"day": 2, "base": "Kyoto", "items": [{"name": "Arashiyama", "category": "sight", "area": "west"}]}]}
    state = {"planning_preferences": {"start_date": TODAY.isoformat()}}
    get, _ = router(holiday_on=TODAY + timedelta(days=1), rain_per_hour=0.5)
    with patch("httpx.AsyncClient.get", get):
        facts = await _conditions(state, copilot)
    assert facts["current_day"]["holidays"] == ["Sports Day"]
    assert facts["current_day"]["weather"]["label"] == "Forecast"
    assert await _conditions({"planning_preferences": {}}, copilot) is None  # no dates: nothing to say
    assert await _conditions({**state, "nav_only": True}, copilot) is None


@pytest.mark.asyncio
async def test_one_shot_drafts_get_forecast_days_and_holidays_only():
    from app.agents.trip_planner.nodes.planning_trip_node import CONDITIONS_TASK, build_plan_generation_prompt
    get, _ = router(holiday_on=TODAY + timedelta(days=1), rain_per_hour=0.5)
    with patch("httpx.AsyncClient.get", get):
        conditions = await enrichment.draft_conditions("Kyoto", TODAY, 12, "JPY")
    days = [f["day"] for f in conditions["forecast"]]
    assert days and max(days) <= 5 and conditions["forecast"][0]["note"].startswith("Rain forecast")  # no typical days
    assert conditions["public_holidays"] == [{"day": 2, "date": (TODAY + timedelta(days=1)).strftime("%a %d %b"), "name": "Sports Day"}]
    assert await enrichment.draft_conditions("Kyoto", None, 3, "JPY") is None

    prompt = build_plan_generation_prompt({"conditions": conditions}, "Kyoto", 12)
    assert '"public_holidays"' in prompt and CONDITIONS_TASK.strip() in prompt
    assert CONDITIONS_TASK.strip() not in build_plan_generation_prompt({}, "Kyoto", 12)


@pytest.mark.asyncio
async def test_agent_facts_focus_on_the_current_day():
    days = [(1, "Kyoto", []), (2, "Kyoto", []), (3, "Kyoto", [])]
    get, _ = router(holiday_on=TODAY + timedelta(days=2), rain_per_hour=0.5)
    with patch("httpx.AsyncClient.get", get):
        result = await run(days)
    facts = enrichment.agent_facts(result, 1)
    assert facts["current_day"]["weather"]["label"] == "Forecast"
    assert facts["current_day"]["weather"]["notes"] and facts["current_day"]["holidays"] == []
    assert "Day 3: Sports Day (public holiday)" in facts["heads_up"]
    assert enrichment.agent_facts(None, 1) is None
