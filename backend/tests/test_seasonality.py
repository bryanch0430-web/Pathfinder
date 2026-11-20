from datetime import date
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

import app.services.seasonality as seasonality
from app.schemas.places import SeasonRequest

KYOTO = [("Arashiyama Bamboo Grove", "Arashiyama Bamboo Grove nature"), ("Nishiki Market", "Nishiki Market market"),
         ("Kinkaku-ji", "Kinkaku-ji temple garden")]
# NASA POWER monthly means for Tokyo (T2M °C, PRECTOTCORR mm/day), trimmed from a live reply.
TOKYO_T2M = [4.7, 5.4, 8.7, 13.5, 17.9, 21.8, 25.5, 26.9, 23.3, 17.6, 12.2, 7.3]
TOKYO_RAIN = [2.6, 3.0, 4.2, 4.5, 4.8, 6.2, 4.9, 4.3, 6.5, 6.0, 3.4, 2.7]


def power_reply(t2m=TOKYO_T2M, rain=TOKYO_RAIN):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"properties": {"parameter": {
        "T2M": dict(zip(seasonality.MONTHS, t2m)), "PRECTOTCORR": dict(zip(seasonality.MONTHS, rain))}}}
    return response


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(seasonality, "CACHE_DB_PATH", tmp_path / "cache.sqlite3")


def texts(tips):
    return " | ".join(note["text"] for note in tips["notes"])


def test_june_tokyo_is_rainy_season_with_an_umbrella_and_indoor_backup():
    tips = seasonality.season_tips(6, 21.8, 6.2, [("Shinjuku Gyoen", "Shinjuku Gyoen park"), ("teamLab", "teamLab museum")])
    assert tips["badge"] == {"tone": "warn", "text": "Rainy season"}
    assert "Pack an umbrella" in texts(tips) and "indoor backup for Shinjuku Gyoen, such as teamLab" in texts(tips)


def test_january_kyoto_flags_outdoor_places_and_suggests_indoor_ones():
    tips = seasonality.season_tips(1, 2.2, 3.1, KYOTO)
    assert tips["badge"] == {"tone": "warn", "text": "Cold"}
    low = next(note for note in tips["notes"] if note["tone"] == "warn")["text"]
    assert "Arashiyama Bamboo Grove and Kinkaku-ji" in low and "Nishiki" not in low
    # Keywords match at word starts only, so a steakhouse called Sparks isn't a park.
    assert not seasonality.OUTDOOR.search("Sparks Steak House restaurant")
    assert any(note["tone"] == "good" and "Nishiki Market" in note["text"] for note in tips["notes"])


def test_heat_snow_and_pleasant_months():
    hot = seasonality.season_tips(5, 34.2, 0.6, [("Amber Fort", "Amber Fort history fort")])
    assert hot["badge"] == {"tone": "warn", "text": "Hot"} and "Do Amber Fort early or late" in texts(hot)
    alpine = seasonality.season_tips(1, -10, 2.0, [("Gornergrat", "Gornergrat mountain viewpoint")])
    assert alpine["badge"]["text"] == "Freezing" and "Snow and ice likely around Gornergrat" in texts(alpine)
    spring = seasonality.season_tips(5, 17.9, 2.0, [("Ueno Park", "Ueno Park park")])
    assert spring["badge"] == {"tone": "good", "text": "Pleasant"} and "good month for Ueno Park" in texts(spring)
    assert seasonality.season_tips(4, 13.5, 4.5, [])["badge"]["text"] == "Showers"


@pytest.mark.asyncio
async def test_climate_is_fetched_once_per_cell_labelled_and_cached():
    request = SeasonRequest(stops=[
        {"id": "a", "lat": 35.68, "lon": 139.69, "month": 6, "places": [{"name": "Shinjuku Gyoen", "category": "park"}]},
        {"id": "b", "lat": 35.66, "lon": 139.70, "month": 1},  # same ~10 km cell
    ])
    get = AsyncMock(return_value=power_reply())
    with patch("httpx.AsyncClient.get", get):
        result = await seasonality.get_season(request)
    assert get.await_count == 1 and get.await_args.kwargs["params"]["parameters"] == "T2M,PRECTOTCORR"
    june, january = result["stops"]
    assert june["label"] == "Typical for June" and june["rain_mm_day"] == 6.2 and june["badge"]["text"] == "Rainy season"
    assert january["badge"]["text"] == "Cold"
    with patch("httpx.AsyncClient.get", AsyncMock(side_effect=AssertionError("network used"))):
        assert len((await seasonality.get_season(request))["stops"]) == 2


@pytest.mark.asyncio
async def test_failures_and_missing_data_are_not_cached_or_shown():
    request = SeasonRequest(stops=[{"id": "a", "lat": 10, "lon": 10, "month": 3}])
    with patch("httpx.AsyncClient.get", AsyncMock(side_effect=httpx.ConnectTimeout("down"))):
        assert (await seasonality.get_season(request))["stops"] == []
    with patch("httpx.AsyncClient.get", AsyncMock(return_value=power_reply(t2m=[-999] * 12))):
        assert (await seasonality.get_season(request))["stops"] == []
    with patch("httpx.AsyncClient.get", AsyncMock(return_value=power_reply())):
        assert len((await seasonality.get_season(request))["stops"]) == 1


@pytest.mark.asyncio
async def test_endpoint_refuses_anonymous_but_serves_guests(client):
    body = {"stops": [{"id": "a", "lat": 35.68, "lon": 139.69, "month": 6}]}
    assert (await client.post("/api/places/season", json=body)).status_code == 401
    guest = {"X-Guest-ID": "3f1c2a9e-8b7d-4c6e-9a1f-2b3c4d5e6f70"}
    with patch("app.api.routes.places.get_season", AsyncMock(return_value={"source": "x", "stops": []})) as service:
        assert (await client.post("/api/places/season", json=body, headers=guest)).status_code == 200
    assert service.await_count == 1
    body["stops"][0]["month"] = 13
    assert (await client.post("/api/places/season", json=body, headers=guest)).status_code == 422


# ---- One-shot planner: trip-wide season facts ----

def test_pick_location_and_trip_months():
    pages = [
        {"title": "Goa (disambiguation)", "index": 1, "coordinates": [{"lat": 1, "lon": 1}], "pageprops": {"disambiguation": ""}},
        {"title": "Goa Express", "index": 2},
        {"title": "Goa", "index": 3, "coordinates": [{"lat": 15.5, "lon": 73.9}]},
    ]
    assert seasonality.pick_location(pages) == {"title": "Goa", "lat": 15.5, "lon": 73.9}
    assert seasonality.pick_location(pages[:2]) is None
    assert seasonality.trip_months(date(2027, 1, 28), 7) == [1, 2]
    assert seasonality.trip_months(date(2027, 6, 10), 0) == [6]


@pytest.mark.asyncio
async def test_trip_season_gives_the_planner_typical_weather_per_month():
    wiki = Mock(); wiki.raise_for_status.return_value = None
    wiki.json.return_value = {"query": {"pages": [{"title": "Tokyo", "index": 1, "coordinates": [{"lat": 35.68, "lon": 139.69}]}]}}
    get = AsyncMock(side_effect=[wiki, power_reply()])
    with patch("httpx.AsyncClient.get", get):
        season = await seasonality.trip_season("Tokyo", date(2027, 5, 30), 5)
    assert season["located_as"] == "Tokyo" and "never describe" in season["how_to_use"].lower()
    assert [m["month"] for m in season["months"]] == ["May", "June"]
    june = season["months"][1]
    assert june["season"] == "Rainy season" and "umbrella" in june["notes"][0]
    # No start date, or nowhere to be found: no season block and no climate call.
    assert await seasonality.trip_season("Tokyo", None, 5) is None
    empty = Mock(); empty.raise_for_status.return_value = None; empty.json.return_value = {"query": {"pages": []}}
    with patch("httpx.AsyncClient.get", AsyncMock(return_value=empty)) as lookup:
        assert await seasonality.trip_season("Nowhereland", date(2027, 6, 1), 3) is None
    assert lookup.await_count == 1


def test_plan_prompt_carries_season_only_when_present():
    from app.agents.trip_planner.nodes.planning_trip_node import build_plan_generation_prompt
    base = {"candidates": [], "research_results": []}
    plain = build_plan_generation_prompt(base, "Tokyo", 5)
    assert "Fit the plan to the season" not in plain and '"season"' not in plain
    seasoned = build_plan_generation_prompt({**base, "season": {"months": [{"month": "June", "season": "Rainy season"}]}}, "Tokyo", 5)
    assert "Fit the plan to the season" in seasoned and "Rainy season" in seasoned
    assert seasoned.index('"season"') < seasoned.index('"research_results"')  # survives context truncation


def test_start_date_parsing():
    from app.services.conversation import _start_date
    assert _start_date({"start_date": "2027-06-10"}) == date(2027, 6, 10)
    assert _start_date({"start_date": None}) is None and _start_date(None) is None and _start_date({"start_date": "soon"}) is None


@pytest.mark.asyncio
async def test_a_failing_or_slow_season_lookup_never_breaks_the_draft(monkeypatch):
    import asyncio
    import app.services.conversation as conversation

    async def broken():
        raise ValueError("odd NASA POWER payload")
    assert await conversation._await_season(asyncio.create_task(broken())) is None

    async def ready():
        return {"months": [{"month": "June"}]}
    assert await conversation._await_season(asyncio.create_task(ready())) == {"months": [{"month": "June"}]}

    monkeypatch.setattr(conversation, "SEASON_WAIT", 0.01)
    slow = asyncio.create_task(asyncio.sleep(1, result={"months": []}))
    assert await conversation._await_season(slow) is None
    assert not slow.cancelled()  # shielded: it keeps going and caches for next time
    slow.cancel()
