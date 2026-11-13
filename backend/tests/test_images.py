"""Pictures: a Google photo reference served through our route (the key stays on the server), a Wikimedia page image
when there is no Google key, and the hotel source's thumbnail. A place with no picture shows none."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from plan_samples import make_plan

from app.application.meals import source_facts, to_alternative
from app.config import Settings
from app.domain.models import GeoPoint, ImageRef, PlaceCandidate
from app.infrastructure.http import override_default_transport, use_transport
from app.infrastructure.providers.hotels import SerpApiHotelSource
from app.infrastructure.providers.images import (
    PHOTO_REF,
    google_image,
    serpapi_image,
    wikimedia_images,
)
from app.infrastructure.providers.places import GooglePlacesSource
from app.interface.api import create_app

FAKE_KEY = "FAKE-KEY-do-not-leak-123"
REF = "places/ChIJabc_-123/photos/AUacShh_-9"
JPEG = b"\xff\xd8\xff\xe0fakejpegbytes"


@pytest.fixture()
def keyed(settings: Settings) -> Settings:
    return settings.model_copy(update={"google_places_api_key": FAKE_KEY})


def _photo(name: str = REF, author: str = "John Smith") -> dict:
    return {
        "name": name,
        "widthPx": 6000,
        "heightPx": 4000,
        "authorAttributions": [
            {
                "displayName": author,
                "uri": "//maps.google.com/maps/contrib/1",
                "photoUri": "//lh3.googleusercontent.com/a",
            }
        ],
    }


# --- the reference check -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref", [REF, "places/A/photos/B", "places/ChIJ-_09/photos/Aa_-9"]
)
def test_a_photo_reference_of_the_documented_shape_passes(ref: str) -> None:
    assert PHOTO_REF.fullmatch(ref)


@pytest.mark.parametrize(
    "ref",
    [
        "places/a/photos/../../v1/other",
        "places/a/photos/b/../c",
        "../places/a/photos/b",
        "https://evil.example/places/a/photos/b",
        "//evil.example/x",
        "places/a/photos/b?key=1",
        "places/a/photos/b\n",
        "places/a/photos/",
        "places//photos/b",
        "",
    ],
)
def test_anything_else_is_not_a_photo_reference(ref: str) -> None:
    assert PHOTO_REF.fullmatch(ref) is None


# --- reading the sources -------------------------------------------------------------------------------------------


def test_a_google_photo_becomes_our_route_url_with_the_author() -> None:
    image = google_image([_photo()])

    assert image == ImageRef(
        url=f"/api/images/place?ref={REF}", source="google", attribution="John Smith"
    )


def test_a_place_without_photos_or_with_a_bad_name_has_no_image() -> None:
    assert google_image(None) is None
    assert google_image([]) is None
    assert google_image([_photo(name="https://evil.example/x")]) is None
    assert google_image([{"widthPx": 1}]) is None


def test_a_photo_without_an_author_has_an_empty_attribution() -> None:
    photo = _photo()
    photo["authorAttributions"] = []

    assert google_image([photo]).attribution == ""


def test_a_hotel_thumbnail_is_kept_only_when_it_is_an_https_address() -> None:
    row = {
        "images": [
            {
                "thumbnail": "https://lh6.googleusercontent.com/p/1",
                "original_image": "https://x.example/o",
            }
        ]
    }

    assert serpapi_image(row) == ImageRef(
        url="https://lh6.googleusercontent.com/p/1", source="serpapi"
    )
    assert serpapi_image({"images": [{"thumbnail": "javascript:alert(1)"}]}) is None
    assert (
        serpapi_image({"images": [{"thumbnail": "http://insecure.example/p"}]}) is None
    )
    assert serpapi_image({"images": []}) is None
    assert serpapi_image({}) is None


def test_a_hotel_candidate_carries_its_thumbnail(settings: Settings) -> None:
    source = SerpApiHotelSource(settings)
    row = {
        "name": "Inn",
        "images": [{"thumbnail": "https://lh6.googleusercontent.com/p/1"}],
    }

    assert source._candidate(row, 1000.0, "JPY").image.source == "serpapi"
    assert source._candidate({"name": "Bare"}, 1000.0, "JPY").image is None


def test_the_google_search_asks_for_photos_and_keeps_the_first(keyed: Settings) -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["mask"] = request.headers.get("x-goog-fieldmask", "")
        row = {
            "id": "p1",
            "displayName": {"text": "Kiyomizu-dera", "languageCode": "en"},
            "location": {"latitude": 34.99, "longitude": 135.78},
            "photos": [_photo()],
        }
        bare = {
            "id": "p2",
            "displayName": {"text": "No Photo Shrine", "languageCode": "en"},
            "location": {"latitude": 34.98, "longitude": 135.77},
        }
        return httpx.Response(200, json={"places": [row, bare]})

    with use_transport(httpx.MockTransport(handler)):
        found = asyncio.run(
            GooglePlacesSource(keyed).search(
                destination="Kyoto",
                geo=GeoPoint(lat=35.0, lng=135.77),
                category="attraction",
                limit=5,
            )
        )

    assert "places.photos" in seen["mask"]
    assert found[0].image.url == f"/api/images/place?ref={REF}"
    assert found[1].image is None
    assert FAKE_KEY not in json.dumps([c.model_dump(mode="json") for c in found])


def test_wikimedia_gives_a_page_image_with_its_author_and_licence(
    settings: Settings,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        if params.get("prop") == "pageimages":
            return httpx.Response(
                200,
                json={
                    "query": {
                        "pages": [
                            {
                                "pageid": 1,
                                "title": "Kinkaku-ji",
                                "thumbnail": {
                                    "source": "https://upload.wikimedia.org/t/Gold.jpg",
                                    "width": 480,
                                    "height": 720,
                                },
                                "pageimage": "Gold.jpg",
                            },
                            {"pageid": 2, "title": "No Picture Temple"},
                        ]
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "query": {
                    "pages": [
                        {
                            "title": "File:Gold.jpg",
                            "imageinfo": [
                                {
                                    "extmetadata": {
                                        "Artist": {
                                            "value": '<a href="//commons.wikimedia.org/x">Jay <b>Cangel</b></a>'
                                        },
                                        "LicenseShortName": {"value": "CC BY-SA 3.0"},
                                    }
                                }
                            ],
                        }
                    ]
                }
            },
        )

    with use_transport(httpx.MockTransport(handler)):
        found = asyncio.run(
            wikimedia_images(["Kinkaku-ji", "No Picture Temple"], settings)
        )

    assert set(found) == {"Kinkaku-ji"}
    assert found["Kinkaku-ji"].source == "wikimedia"
    assert found["Kinkaku-ji"].url == "https://upload.wikimedia.org/t/Gold.jpg"
    assert found["Kinkaku-ji"].attribution == "Jay Cangel, CC BY-SA 3.0"


def test_a_failing_wikimedia_gives_no_images_and_no_error(settings: Settings) -> None:
    with use_transport(httpx.MockTransport(lambda request: httpx.Response(500))):
        assert asyncio.run(wikimedia_images(["Kinkaku-ji"], settings)) == {}
    assert asyncio.run(wikimedia_images([], settings)) == {}


# --- the plan --------------------------------------------------------------------------------------------------------


def test_a_row_with_a_picture_gives_a_meal_and_an_alternative_with_it() -> None:
    row = {
        "name": "Ramen Ya",
        "geo": {"lat": 35.0, "lng": 135.7},
        "image": {
            "url": "/api/images/place?ref=" + REF,
            "source": "google",
            "attribution": "John",
        },
    }

    assert source_facts(row)["image"].source == "google"
    assert to_alternative(row, origin=None).image.attribution == "John"


def test_a_row_without_a_picture_gives_none() -> None:
    row = {"name": "Ramen Ya", "geo": {"lat": 35.0, "lng": 135.7}}

    assert source_facts(row)["image"] is None
    assert to_alternative(row, origin=None).image is None


def test_a_place_candidate_without_a_photo_has_no_image() -> None:
    assert PlaceCandidate(name="X").image is None


def test_a_plan_never_holds_the_key(keyed: Settings) -> None:
    plan = make_plan()
    plan.days[0].items[0].image = google_image([_photo()])

    assert FAKE_KEY not in plan.model_dump_json()
    assert plan.days[0].items[0].image.url.startswith("/api/images/place?ref=places/")


# --- the route -------------------------------------------------------------------------------------------------------


def _client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def test_a_bad_reference_is_refused_with_422_and_nothing_is_fetched(
    keyed: Settings,
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    with (
        override_default_transport(httpx.MockTransport(handler)),
        _client(keyed) as client,
    ):
        for bad in [
            "places/a/photos/../../x",
            "https://evil.example/x",
            "places/a/photos/b?key=1",
            "",
        ]:
            assert (
                client.get("/api/images/place", params={"ref": bad}).status_code == 422
            )
        assert client.get("/api/images/place").status_code == 422

    assert calls == []


def test_the_photo_is_fetched_with_the_key_cached_and_served_for_a_week(
    keyed: Settings,
) -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    with (
        override_default_transport(httpx.MockTransport(handler)),
        _client(keyed) as client,
    ):
        first = client.get("/api/images/place", params={"ref": REF})
        second = client.get("/api/images/place", params={"ref": REF})

    assert first.status_code == second.status_code == 200
    assert first.content == second.content == JPEG
    assert first.headers["content-type"] == "image/jpeg"
    assert "max-age=604800" in first.headers["cache-control"]
    assert len(calls) == 1, "the second call is served from the disk cache"
    assert calls[0].url.path == f"/v1/{REF}/media"
    assert dict(calls[0].url.params)["maxWidthPx"]
    for response in (first, second):
        assert FAKE_KEY not in json.dumps(dict(response.headers))
        assert FAKE_KEY.encode() not in response.content


def test_without_a_google_key_the_route_answers_404(settings: Settings) -> None:
    with _client(settings.model_copy(update={"google_places_api_key": ""})) as client:
        assert client.get("/api/images/place", params={"ref": REF}).status_code == 404


def test_a_failing_source_gives_a_plain_error_without_the_key(
    keyed: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"boom {request.url}")

    with (
        override_default_transport(httpx.MockTransport(handler)),
        _client(keyed) as client,
    ):
        response = client.get("/api/images/place", params={"ref": REF})

    assert response.status_code == 502
    assert FAKE_KEY not in response.text
    assert FAKE_KEY not in caplog.text


def test_something_that_is_not_an_image_is_not_served(keyed: Settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"<html>hi</html>", headers={"content-type": "text/html"}
        )

    with (
        override_default_transport(httpx.MockTransport(handler)),
        _client(keyed) as client,
    ):
        assert client.get("/api/images/place", params={"ref": REF}).status_code == 502


def test_the_route_is_rate_limited(keyed: Settings) -> None:
    limited = keyed.model_copy(update={"rate_limit_lookup_per_minute": 2})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    with (
        override_default_transport(httpx.MockTransport(handler)),
        _client(limited) as client,
    ):
        codes = [
            client.get("/api/images/place", params={"ref": REF}).status_code
            for _ in range(4)
        ]

    assert codes[:2] == [200, 200] and 429 in codes[2:]


# --- the Wikipedia title a place source gives ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "title"),
    [
        ("en:Fushimi Inari-taisha", "Fushimi Inari-taisha"),
        ("Kinkaku-ji", "Kinkaku-ji"),
        ("ja:金閣寺", ""),
        ("en:<script>", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_only_an_english_plain_wikipedia_title_is_kept(
    value: object, title: str
) -> None:
    from app.infrastructure.providers.places import _wikipedia_title

    assert _wikipedia_title(value) == title


def test_places_with_a_title_get_their_page_image_in_one_lookup(
    settings: Settings,
) -> None:
    from app.infrastructure.providers.places import add_wikimedia_images

    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if dict(request.url.params).get("prop") == "pageimages":
            page = {
                "title": "Kinkaku-ji",
                "pageimage": "Gold.jpg",
                "thumbnail": {"source": "https://upload.wikimedia.org/t/Gold.jpg"},
            }
            return httpx.Response(200, json={"query": {"pages": [page]}})
        return httpx.Response(200, json={"query": {"pages": []}})

    places = [
        PlaceCandidate(name="Golden Pavilion", wikipedia_title="Kinkaku-ji"),
        PlaceCandidate(name="Corner Cafe"),
    ]
    with use_transport(httpx.MockTransport(handler)):
        asyncio.run(add_wikimedia_images(places, settings))

    assert places[0].image.source == "wikimedia" and places[1].image is None
    assert (
        len([c for c in calls if dict(c.url.params).get("prop") == "pageimages"]) == 1
    )


# --- a planned trip ----------------------------------------------------------------------------------------------------


class _PicturedPlaces:
    """The sandbox places, each given a Google photo except those whose name ends in 'Bare'."""

    name = "sandbox-pictured-places"

    async def search(self, **kwargs):
        from app.evaluation import SandboxPlaces

        found = await SandboxPlaces(kwargs["destination"]).search(**kwargs)
        for index, place in enumerate(found):
            place.image = google_image(
                [_photo(name=f"places/PID{index}/photos/PH{index}")]
            )
        return found


class _PicturedHotels:
    name = "sandbox-pictured-hotels"

    async def search(self, **kwargs):
        from app.evaluation import SandboxHotels

        found = await SandboxHotels(kwargs["destination"]).search(**kwargs)
        for hotel in found:
            hotel.image = ImageRef(
                url="https://lh6.googleusercontent.com/p/thumb", source="serpapi"
            )
        return found


def test_pictures_reach_stops_meals_and_stays_of_a_planned_trip(
    keyed: Settings,
) -> None:
    from datetime import timedelta

    from single_city_plan import START

    from app.domain.models import EvalCase
    from app.eval_efficiency import build_rig, case_context
    from app.eval_metering import RunLedger

    async def main():
        ledger = RunLedger()
        try:
            rig = build_rig(
                keyed.model_copy(
                    update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8}
                ),
                "Kyoto",
                ledger,
            )
            rig.plan_path.registry.places = [_PicturedPlaces()]
            rig.plan_path.registry.hotels = [_PicturedHotels()]
            rig.llm.route = {"intent": "plan", "confidence": 0.95}
            session = ledger.store.create_session("guest-pictures")
            case = EvalCase(
                id="p",
                dataset="d",
                input="",
                destination="Kyoto",
                days=3,
                party_size=2,
                budget=90000,
                currency="HKD",
            )
            context = case_context(case, session.id)
            context.start_date, context.end_date = START, START + timedelta(days=2)
            ledger.store.save_context(context)
            _ = [
                e
                async for e in rig.turns.handle_turn(
                    session_id=session.id, message="Plan this trip"
                )
            ]
            return ledger.store.get_plan(session.id)
        finally:
            ledger.close()

    plan = asyncio.run(main())

    stops = [item for _, item in plan.iter_items()]
    assert any(item.type == "attraction" and item.image for item in stops)
    assert any(item.type == "meal" and item.image for item in stops)
    assert plan.hotels[0].image.source == "serpapi"
    assert all(
        item.image is None
        or item.image.url.startswith(("/api/images/place?ref=places/", "https://"))
        for item in stops
    )
    assert FAKE_KEY not in plan.model_dump_json()


def test_alternatives_of_a_meal_row_keep_their_pictures() -> None:
    from app.application.meals import alternatives_for

    photo = {
        "url": "/api/images/place?ref=" + REF,
        "source": "google",
        "attribution": "John",
    }
    row = {
        "name": "Ramen Ya",
        "geo": {"lat": 35.0, "lng": 135.7},
        "alternatives": [
            {"name": "Soba Ya", "geo": {"lat": 35.001, "lng": 135.7}, "image": photo},
            {"name": "Udon Ya", "geo": {"lat": 35.002, "lng": 135.7}},
        ],
    }

    first, second = alternatives_for(row)

    assert first.image.attribution == "John" and second.image is None
