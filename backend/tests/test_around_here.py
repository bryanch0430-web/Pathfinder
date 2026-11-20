from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

import app.services.around_here as around
import app.services.place_media as place_media

SHIBUYA = (35.66, 139.70)
WIKITEXT = """
==See==
* {{see
| name=Shibuya Crossing | alt=渋谷スクランブル交差点 | lat=35.6595 | long=139.7005
| hours=Always open | price=Free {{ref|x}} | wikidata=Q1 | image=Crossing.jpg
| content=The '''busiest''' crossing in the world. Up to 3,000 people cross at a time.
}}
* {{do | name=[[Hachikō|Hachiko]] statue | lat=35.6590 | long=139.7006 | content=Meet here.}}
* {{see | name=Far Temple | lat=35.80 | long=139.90 | content=Too far to count.}}
* {{see | alt=No name here | lat=35.66 | long=139.70}}
==Eat==
{{eatpricerange|Under ¥1000|¥1000-3000|Over ¥3000}}
* {{listing | type=eat | name=Kujiraya | price=&yen;1,500 | content=Whale, for the curious.}}
* {{sleep | name=Hotel Nowhere | lat= | long= | url=hotel.example | content=A bed.}}
"""


def reply(payload):
    response = Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def fake_wikis(url, params):
    """One fake per request the service makes, routed by what it asks for."""
    if params.get("list") == "geosearch":
        return reply({"query": {"geosearch": [{"title": "Tokyo/Shibuya", "dist": 500}]}})
    if params.get("rvprop") == "content":
        return reply({"query": {"pages": [{"title": "Tokyo/Shibuya", "revisions": [{"slots": {"main": {"content": WIKITEXT}}}]}]}})
    if params.get("generator") == "geosearch":
        return reply({"query": {"pages": [
            {"title": "Shibuya Crossing", "pageimage": "Wiki_crossing.jpg", "coordinates": [{"lat": 35.6595, "lon": 139.7005}],
             "pageprops": {"wikibase_item": "Q1"}, "fullurl": "https://en.wikipedia.org/wiki/Shibuya_Crossing"},
            {"title": "Shibuya Station", "description": "railway station in Tokyo", "pageimage": "Station.jpg",
             "coordinates": [{"lat": 35.658, "lon": 139.7016}], "pageprops": {"wikibase_item": "Q2"}},
            {"title": "Tokyo Lok Sabha constituency", "coordinates": [{"lat": 35.66, "lon": 139.70}]},
        ]}})
    if params.get("prop") == "pageimages|coordinates":
        return reply({"query": {
            "redirects": [{"from": "Hachiko statue", "to": "Hachikō statue"}],
            "pages": [{"title": "Hachikō statue", "pageimage": "Hachiko.jpg", "coordinates": [{"lat": 35.659, "lon": 139.7006}]}],
        }})
    if params.get("prop") == "imageinfo":
        files = [title.removeprefix("File:") for title in params["titles"].split("|")]
        return reply({"query": {"pages": [{"title": f"File:{name}", "imageinfo": [{
            "thumburl": f"https://upload.wikimedia.org/thumb/{name}", "descriptionurl": f"https://commons.wikimedia.org/wiki/File:{name}",
            "extmetadata": {"Artist": {"value": "Ann"}, "LicenseShortName": {"value": "CC BY 4.0"}},
        }]} for name in files]}})
    raise AssertionError(f"unexpected request {params}")


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(around, "CACHE_DB_PATH", tmp_path / "cache.sqlite3")
    monkeypatch.setattr(place_media, "CACHE_DB_PATH", tmp_path / "cache.sqlite3")


def test_parses_listing_templates_into_plain_fields():
    listings = {item["name"]: item for item in around.parse_listings(WIKITEXT)}
    assert set(listings) == {"Shibuya Crossing", "Hachiko statue", "Far Temple", "Kujiraya", "Hotel Nowhere"}
    crossing = listings["Shibuya Crossing"]
    assert crossing["kind"] == "see" and crossing["price"] == "Free" and crossing["wikidata"] == "Q1"
    assert crossing["content"].startswith("The busiest crossing")
    assert listings["Kujiraya"]["kind"] == "eat" and listings["Kujiraya"]["price"] == "¥1,500"
    assert around._first_sentence(crossing["content"]) == "The busiest crossing in the world."


@pytest.mark.asyncio
async def test_merges_sources_filters_far_listings_and_credits_every_photo():
    get = AsyncMock(side_effect=lambda url, params: fake_wikis(url, params))
    with patch("httpx.AsyncClient.get", get):
        result = await around.get_around_here(*SHIBUYA)
    items = {item["name"]: item for item in result["items"]}
    # The far temple is dropped; the constituency isn't a place; the listing and the article merge by Wikidata id.
    assert set(items) == {"Shibuya Crossing", "Hachiko statue", "Kujiraya", "Hotel Nowhere", "Shibuya Station"}
    crossing = items["Shibuya Crossing"]
    assert crossing["source"] == "Wikivoyage" and crossing["hours"] == "Always open"
    assert crossing["image"].endswith("Crossing.jpg") and crossing["image_credit"]["license"] == "CC BY 4.0"
    assert items["Hachiko statue"]["image"].endswith("Hachiko.jpg")  # borrowed through a redirect
    assert items["Shibuya Station"]["kind"] == "transit" and items["Shibuya Station"]["tab"] == "landmarks"
    assert items["Kujiraya"]["image"] is None and items["Hotel Nowhere"]["website"] is None
    assert result["guides"] == [{"title": "Tokyo/Shibuya", "url": "https://en.wikivoyage.org/wiki/Tokyo/Shibuya"}]
    assert not any(call.kwargs["params"].get("action") == "wbgetentities" for call in get.await_args_list)  # Q1 had a photo


@pytest.mark.asyncio
async def test_areas_are_cached_and_failures_are_retried():
    with patch("httpx.AsyncClient.get", AsyncMock(side_effect=httpx.ConnectTimeout("down"))):
        assert (await around.get_around_here(*SHIBUYA))["available"] is False
    with patch("httpx.AsyncClient.get", AsyncMock(side_effect=lambda url, params: fake_wikis(url, params))):
        assert len((await around.get_around_here(*SHIBUYA))["items"]) == 5
    with patch("httpx.AsyncClient.get", AsyncMock(side_effect=AssertionError("network used"))):
        assert len((await around.get_around_here(35.6601, 139.7049))["items"]) == 5  # same ~1 km cell


@pytest.mark.asyncio
async def test_endpoint_refuses_anonymous_but_serves_guests(client):
    assert (await client.get("/api/places/around", params={"lat": 35.66, "lon": 139.7})).status_code == 401
    with patch("app.api.routes.places.get_around_here", AsyncMock(return_value={"items": [], "guides": [], "available": True})) as service:
        response = await client.get("/api/places/around", params={"lat": 35.66, "lon": 139.7},
                                    headers={"X-Guest-ID": "3f1c2a9e-8b7d-4c6e-9a1f-2b3c4d5e6f70"})
    assert response.status_code == 200 and service.await_count == 1
    bad = await client.get("/api/places/around", params={"lat": 95, "lon": 139.7},
                           headers={"X-Guest-ID": "3f1c2a9e-8b7d-4c6e-9a1f-2b3c4d5e6f70"})
    assert bad.status_code == 422
