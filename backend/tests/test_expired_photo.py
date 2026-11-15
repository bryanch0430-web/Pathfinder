"""An expired Google photo name: the place is looked up again once, the new photo is cached and served.

No real network: every call goes to a fake Places source behind a mock transport."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.infrastructure.http import override_default_transport
from app.interface.api import create_app

FAKE_KEY = "FAKE-KEY-do-not-leak-456"
PLACE = "places/ChIJexpired_-1"
OLD = f"{PLACE}/photos/OLDname_-1"
NEW = f"{PLACE}/photos/NEWname_-2"
OLD_BYTES = b"\xff\xd8\xff\xe0old"
NEW_BYTES = b"\xff\xd8\xff\xe0new"


@pytest.fixture()
def keyed(settings: Settings) -> Settings:
    return settings.model_copy(update={"google_places_api_key": FAKE_KEY})


class FakePlaces:
    """A fake Places source: which photo names are live, what the place lookup answers."""

    def __init__(
        self,
        *,
        live: dict[str, bytes] | None = None,
        lookup: httpx.Response | None = None,
        media_status: int = 404,
    ) -> None:
        self.live = {NEW: NEW_BYTES} if live is None else live
        self.lookup = lookup or httpx.Response(
            200, json={"photos": [{"name": NEW}, {"name": f"{PLACE}/photos/other"}]}
        )
        self.media_status = media_status
        self.calls: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        path = request.url.path
        if path.endswith("/media"):
            name = path.removeprefix("/v1/").removesuffix("/media")
            if name in self.live:
                return httpx.Response(
                    200, content=self.live[name], headers={"content-type": "image/jpeg"}
                )
            return httpx.Response(self.media_status)
        if path == f"/v1/{PLACE}":
            return self.lookup
        return httpx.Response(500)

    @property
    def paths(self) -> list[str]:
        return [call.url.path for call in self.calls]

    @property
    def lookups(self) -> list[httpx.Request]:
        return [call for call in self.calls if not call.url.path.endswith("/media")]


def _get(settings: Settings, source: FakePlaces, *refs: str) -> list[httpx.Response]:
    with (
        override_default_transport(httpx.MockTransport(source)),
        TestClient(create_app(settings)) as client,
    ):
        return [client.get("/api/images/place", params={"ref": ref}) for ref in refs]


def test_an_expired_name_is_looked_up_again_and_the_new_photo_is_served(
    keyed: Settings,
) -> None:
    source = FakePlaces()

    (response,) = _get(keyed, source, OLD)

    assert response.status_code == 200
    assert response.content == NEW_BYTES
    assert response.headers["content-type"] == "image/jpeg"
    assert source.paths == [
        f"/v1/{OLD}/media",
        f"/v1/{PLACE}",
        f"/v1/{NEW}/media",
    ]
    (lookup,) = source.lookups
    assert lookup.headers["x-goog-api-key"] == FAKE_KEY
    assert lookup.headers["x-goog-fieldmask"] == "photos"
    assert FAKE_KEY not in response.text and FAKE_KEY not in str(response.headers)


def test_the_new_photo_is_cached_for_the_old_name_and_for_its_own(
    keyed: Settings,
) -> None:
    source = FakePlaces()

    first, again, by_new_name = _get(keyed, source, OLD, OLD, NEW)

    assert [r.status_code for r in (first, again, by_new_name)] == [200, 200, 200]
    assert first.content == again.content == by_new_name.content == NEW_BYTES
    assert len(source.calls) == 3, "the later requests are served from the disk cache"


def test_a_cached_old_photo_is_served_without_any_lookup(keyed: Settings) -> None:
    source = FakePlaces(live={OLD: OLD_BYTES})

    first, second = _get(keyed, source, OLD, OLD)

    assert first.content == second.content == OLD_BYTES
    assert source.paths == [f"/v1/{OLD}/media"]


def test_the_lookup_is_made_once_and_a_second_refusal_is_the_error_as_before(
    keyed: Settings,
) -> None:
    source = FakePlaces(live={})

    (response,) = _get(keyed, source, OLD)

    assert response.status_code == 502
    assert response.json()["detail"] == "The photo could not be fetched."
    assert source.paths == [f"/v1/{OLD}/media", f"/v1/{PLACE}", f"/v1/{NEW}/media"]
    assert FAKE_KEY not in response.text


def test_a_failed_lookup_gives_the_same_502(keyed: Settings) -> None:
    source = FakePlaces(lookup=httpx.Response(404))

    (response,) = _get(keyed, source, OLD)

    assert response.status_code == 502
    assert source.paths == [f"/v1/{OLD}/media", f"/v1/{PLACE}"]


def test_a_place_with_no_photos_gives_the_same_502(keyed: Settings) -> None:
    source = FakePlaces(lookup=httpx.Response(200, json={"photos": []}))

    (response,) = _get(keyed, source, OLD)

    assert response.status_code == 502
    assert source.paths == [f"/v1/{OLD}/media", f"/v1/{PLACE}"]


def test_a_lookup_that_gives_the_same_name_is_not_fetched_again(
    keyed: Settings,
) -> None:
    source = FakePlaces(lookup=httpx.Response(200, json={"photos": [{"name": OLD}]}))

    (response,) = _get(keyed, source, OLD)

    assert response.status_code == 502
    assert source.paths == [f"/v1/{OLD}/media", f"/v1/{PLACE}"]


def test_a_lookup_that_names_something_else_is_not_fetched(keyed: Settings) -> None:
    source = FakePlaces(
        lookup=httpx.Response(
            200, json={"photos": [{"name": "places/other/photos/x"}, {"name": 5}]}
        )
    )

    (response,) = _get(keyed, source, OLD)

    assert response.status_code == 502
    assert source.paths == [f"/v1/{OLD}/media", f"/v1/{PLACE}"]


def test_a_server_error_is_not_an_expired_name_and_looks_nothing_up(
    keyed: Settings,
) -> None:
    source = FakePlaces(media_status=500)

    (response,) = _get(keyed, source, OLD)

    assert response.status_code == 502
    assert source.paths == [f"/v1/{OLD}/media"]


def test_a_failing_lookup_connection_leaks_no_key(
    keyed: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/media"):
            return httpx.Response(404)
        raise httpx.ConnectError(f"boom {request.url} {request.headers}")

    with (
        override_default_transport(httpx.MockTransport(handler)),
        TestClient(create_app(keyed)) as client,
    ):
        response = client.get("/api/images/place", params={"ref": OLD})

    assert response.status_code == 502
    assert FAKE_KEY not in response.text
    assert FAKE_KEY not in caplog.text
