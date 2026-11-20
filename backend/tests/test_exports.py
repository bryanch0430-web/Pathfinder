from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

import app.services.enrichment as enrichment

HITS = {
    "kyoto": (35.0116, 135.7681),
    "fushimi inari, kyoto": (34.9671, 135.7727),
    "springfield, kyoto": (39.80, -89.65),  # a namesake far away: must be dropped
    "springfield, japan": (39.80, -89.65),
    "nishiki market, japan": (35.0050, 135.7649),  # found only with the destination as context
}


def fake_nominatim():
    calls = []
    real_get = httpx.AsyncClient.get

    async def get(self, url, params=None, **kwargs):
        if "nominatim" not in str(url):
            return await real_get(self, url, params=params, **kwargs)
        calls.append(params["q"])
        hit = HITS.get(params["q"].casefold())
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = [{"lat": str(hit[0]), "lon": str(hit[1]), "address": {"country_code": "jp"}}] if hit else []
        return response

    return get, calls


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(enrichment, "CACHE_DB_PATH", tmp_path / "cache.sqlite3")
    monkeypatch.setattr(enrichment, "NOMINATIM_INTERVAL", 0)


@pytest.mark.asyncio
async def test_places_are_located_near_their_base_and_namesakes_dropped():
    get, calls = fake_nominatim()
    places = [("d1-0", "Fushimi Inari", "Kyoto"), ("d1-1", "Springfield", "Kyoto"), ("d2-0", "Nishiki Market", "Kyoto")]
    with patch("httpx.AsyncClient.get", get):
        found = await enrichment.locate_places("Japan", places)
    assert [p["id"] for p in found] == ["d1-0", "d2-0"]
    assert calls.count("Kyoto, Japan") == 1  # the base is located once for all its places

    with patch("httpx.AsyncClient.get", AsyncMock(side_effect=AssertionError("network used"))):
        assert await enrichment.locate_places("Japan", places) == found  # hits and misses both cached


@pytest.mark.asyncio
async def test_time_budget_returns_what_was_found(monkeypatch):
    monkeypatch.setattr(enrichment, "POINTS_BUDGET_SECONDS", -1)
    get, calls = fake_nominatim()
    with patch("httpx.AsyncClient.get", get):
        assert await enrichment.locate_places("Kyoto", [("d1-0", "Fushimi Inari", "Kyoto")]) == []
    assert calls == []


@pytest.mark.asyncio
async def test_endpoint_serves_guests_refuses_anonymous_and_caps_the_batch(client):
    get, _ = fake_nominatim()
    body = {"destination": "Kyoto", "places": [{"id": "d1-0", "name": "Fushimi Inari", "base": "Kyoto"}]}
    with patch("httpx.AsyncClient.get", get):
        assert (await client.post("/api/exports/points", json=body)).status_code == 401
        response = await client.post("/api/exports/points", json=body, headers={"X-Guest-ID": "5b0c1c9e-0000-4000-8000-00000000e002"})
    assert response.status_code == 200
    assert response.json()["points"] == [{"id": "d1-0", "lat": 34.9671, "lon": 135.7727}]
    too_many = {"places": [{"id": f"p{i}", "name": "x", "base": "y"} for i in range(enrichment.MAX_POINTS + 1)]}
    assert (await client.post("/api/exports/points", json=too_many, headers={"X-Guest-ID": "5b0c1c9e-0000-4000-8000-00000000e002"})).status_code == 422
