from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest

import app.services.place_media as place_media
from app.schemas.places import PlaceMediaItem, PlaceMediaRequest

KYOTO = {"lat": 35.0116, "lon": 135.7681}


def page(title, index, lat=None, lon=None, thumb=True, disambiguation=False, file="Photo.jpg"):
    item = {"title": title, "index": index, "fullurl": f"https://en.wikipedia.org/wiki/{title}",
            "extract": f"{title} is a place.", "pageimage": file}
    if lat is not None:
        item["coordinates"] = [{"lat": lat, "lon": lon}]
    if thumb:
        item["thumbnail"] = {"source": f"https://upload.wikimedia.org/{file}", "width": 800, "height": 533}
    if disambiguation:
        item["pageprops"] = {"disambiguation": ""}
    return item


def reply(payload):
    response = Mock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def search_reply(*pages):
    return reply({"query": {"pages": list(pages)}})


def credit_reply(*files, non_free=()):
    return reply({"query": {"pages": [{
        "title": f"File:{name}",
        "imageinfo": [{"descriptionurl": f"https://commons.wikimedia.org/wiki/File:{name}", "extmetadata": {
            "Artist": {"value": '<a href="//commons.wikimedia.org/wiki/User:Ann">Ann &amp; Bo</a>'},
            "LicenseShortName": {"value": "CC BY-SA 4.0"},
            "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
            "NonFree": {"value": "true" if name in non_free else "false"},
        }}],
    } for name in files]}})


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(place_media, "CACHE_DB_PATH", tmp_path / "place_media.sqlite3")


def test_pick_rejects_far_namesakes_disambiguation_and_photoless_pages():
    place = PlaceMediaItem(id="s", name="Springfield", **KYOTO)
    pages = [
        page("Springfield (disambiguation)", 1, 35.01, 135.76, disambiguation=True),
        page("Springfield, Illinois", 2, 39.8, -89.65),
        page("Kyoto Tower", 3, 34.98, 135.76, thumb=False),
        page("Kiyomizu-dera", 4, 34.9949, 135.785),
    ]
    assert place_media.pick_candidate(place, pages)["title"] == "Kiyomizu-dera"
    assert place_media.pick_candidate(place, pages[:3]) is None


def test_pick_requires_coordinates_only_when_the_stop_has_them():
    no_coords = page("Hama-rikyū Gardens", 1)
    assert place_media.pick_candidate(PlaceMediaItem(id="h", name="Hamarikyu Garden"), [no_coords])
    assert place_media.pick_candidate(PlaceMediaItem(id="h", name="Hamarikyu Garden", **KYOTO), [no_coords]) is None


@pytest.mark.asyncio
async def test_returns_credited_photos_and_drops_non_free_files():
    request = PlaceMediaRequest(places=[
        {"id": "a", "name": "Arashiyama", "lat": 35.0094, "lon": 135.6668},
        {"id": "b", "name": "Kyoto Station", "lat": 34.9858, "lon": 135.7588},
    ])
    get = AsyncMock(side_effect=[
        search_reply(page("Arashiyama", 1, 35.0094, 135.6668, file="Arashiyama_bamboo.jpg")),
        search_reply(page("Kyoto Station", 1, 34.9858, 135.7588, file="Logo.svg")),
        credit_reply("Arashiyama bamboo.jpg", "Logo.svg", non_free=("Logo.svg",)),
    ])
    with patch("httpx.AsyncClient.get", get):
        result = await place_media.get_place_media(request)
    assert [item["id"] for item in result["places"]] == ["a"]
    credit = result["places"][0]["credit"]
    assert credit["author"] == "Ann & Bo" and credit["license"] == "CC BY-SA 4.0"
    assert get.await_args_list[0].kwargs["params"]["pilicense"] == "free"


@pytest.mark.asyncio
async def test_cache_hits_skip_the_network_and_failures_are_retried():
    request = PlaceMediaRequest(places=[{"id": "a", "name": "Arashiyama", "lat": 35.0094, "lon": 135.6668}])
    with patch("httpx.AsyncClient.get", AsyncMock(side_effect=httpx.ConnectTimeout("down"))):
        assert (await place_media.get_place_media(request))["places"] == []
    ok = AsyncMock(side_effect=[
        search_reply(page("Arashiyama", 1, 35.0094, 135.6668, file="A.jpg")), credit_reply("A.jpg"),
    ])
    with patch("httpx.AsyncClient.get", ok):
        assert len((await place_media.get_place_media(request))["places"]) == 1
    with patch("httpx.AsyncClient.get", AsyncMock(side_effect=AssertionError("network used"))):
        assert (await place_media.get_place_media(request))["places"][0]["title"] == "Arashiyama"


@pytest.mark.asyncio
async def test_misses_are_cached_so_unknown_places_are_not_searched_again():
    request = PlaceMediaRequest(places=[{"id": "x", "name": "Nowhere Street", **KYOTO}])
    search = AsyncMock(return_value=search_reply())
    with patch("httpx.AsyncClient.get", search):
        await place_media.get_place_media(request)
        await place_media.get_place_media(request)
    assert search.await_count == 1


@pytest.mark.asyncio
async def test_endpoint_refuses_anonymous_but_serves_guests(client):
    body = {"places": [{"id": "a", "name": "Kyoto"}]}
    assert (await client.post("/api/places/media", json=body)).status_code == 401
    with patch("app.api.routes.places.get_place_media", AsyncMock(return_value={"provider": "wikipedia", "places": []})) as service:
        response = await client.post("/api/places/media", json=body,
                                     headers={"X-Guest-ID": "3f1c2a9e-8b7d-4c6e-9a1f-2b3c4d5e6f70"})
    assert response.status_code == 200 and service.await_count == 1
