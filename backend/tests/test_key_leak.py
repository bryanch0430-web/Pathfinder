"""The Google Places key must never reach a browser or the database."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import misc
from app.api.app import create_app
from app.config import Settings
from app.core.store import Store
from app.domain.models import ImageRef, Money, SourceRef, TripContext
from app.evidence.models import Bundle
from app.evidence.providers import Providers
from app.plan.builder import build_plan
from app.security.redact import install_log_redaction, redact_secrets, scrub_text
from app.understand.schemas import DayChoice, PlanDraft, StopChoice

PLACES_KEY = "SENTINEL-places-0001"
PHOTO = "places/ChIJabc_123-X/photos/AUy1YQ_photo-Token9"
KEYED_URL = f"https://places.googleapis.com/v1/{PHOTO}/media?maxWidthPx=900&key={PLACES_KEY}"
SENTINELS = {
    "google_places_api_key": PLACES_KEY,
    "serpapi_key": "SENTINEL-serp-0002",
    "youtube_api_key": "SENTINEL-youtube-0003",
    "verifier_api_key": "SENTINEL-verifier-0004",
    "decision_api_key": "SENTINEL-decision-0005",
    "llm_api_key": "SENTINEL-llm-0006",
    "openai_api_key": "SENTINEL-openai-0007",
    "poe_api_key": "SENTINEL-poe-0008",
    "xai_api_key": "SENTINEL-xai-0009",
    "deepseek_search_api_key": "SENTINEL-deepseek-0010",
}
MAPS_KEY = "SENTINEL-maps-0011"  # the browser map key is meant to be sent to the page


def _settings(tmp_path, **extra) -> Settings:
    return Settings(
        database_path=str(tmp_path / "leak.db"),
        sandbox=True,
        local_admin=True,
        admin_token="test-admin",
        secret_key="test-secret",
        **{**SENTINELS, **extra},
    )


def _place(index: int = 0, kind: str = "restaurant") -> dict:
    return {
        "displayName": {"text": f"Place {index}"},
        "location": {"latitude": 35.0 + index / 100, "longitude": 135.0},
        "types": [kind],
        "photos": [{"name": PHOTO}],
        "formattedAddress": "1 Test Street",
    }


def _evidence_bundle(tmp_path) -> Bundle:
    providers = Providers(_settings(tmp_path))
    items = [providers._place_to_evidence(_place(i, "attraction"), "q", i) for i in range(3)]
    meal = providers._place_to_evidence(_place(7, "restaurant"), "q", 7)
    hotel = providers._place_to_evidence(_place(8, "lodging"), "q", 8)
    bundle = Bundle()
    bundle.add([*items, meal, hotel], "test")
    for item in bundle.items:
        item.source_refs = [SourceRef(title=item.name, url="https://example.test", source="Test")]
        item.price = Money(amount=500, currency="JPY")
        item.price_kind = "reference"
    return bundle


def _plan(tmp_path):
    bundle = _evidence_bundle(tmp_path)
    refs = [item.ref for item in bundle.items if item.kind == "attraction"]
    hotel = next(item.ref for item in bundle.items if item.kind == "hotel")
    draft = PlanDraft(
        summary="One day.",
        days=[
            DayChoice(
                date="2027-04-10",
                title="Day",
                stops=[
                    StopChoice(ref=refs[0], start="09:00", end="10:30"),
                    StopChoice(ref=refs[1], start="11:00", end="12:00"),
                ],
            )
        ],
        hotel_ref=hotel,
        ticket_refs=[],
        confidence=0.8,
    )
    context = TripContext(
        session_id="s1",
        destination="Kyoto",
        origin="Hong Kong",
        start_date="2027-04-10",
        end_date="2027-04-10",
        days=1,
        party_size=1,
        budget=200000,
        currency="JPY",
    )
    return build_plan(
        context=context,
        bundle=bundle,
        draft=draft,
        disruptions=[],
        weather_by_date={},
        start=date(2027, 4, 10),
        end=date(2027, 4, 10),
        settings=_settings(tmp_path),
    )


def _old_body(tmp_path) -> str:
    """A plan as an earlier build stored it: picture addresses carrying the key."""
    body = _plan(tmp_path).model_dump_json()
    assert "/api/images/google?" in body
    old = KEYED_URL.replace("&", "\\u0026")
    return re.sub(r"/api/images/google\?photo=[^\"]+", lambda _m: old, body)


# -- (i) built evidence and plans -------------------------------------------------


def test_evidence_image_carries_no_key(tmp_path):
    evidence = Providers(_settings(tmp_path))._place_to_evidence(_place(), "q", 0)
    url = evidence.image.url
    assert url.startswith("/api/images/google?photo=places%2F")
    assert "key=" not in url and PLACES_KEY not in url


def test_plan_json_has_no_key_anywhere(tmp_path):
    dumped = _plan(tmp_path).model_dump_json()
    assert "/api/images/google?" in dumped  # the pictures are still there
    assert "key=" not in dumped.lower()
    for value in SENTINELS.values():
        assert value not in dumped


def test_an_image_ref_built_from_an_old_address_is_cleaned():
    image = ImageRef(url=KEYED_URL, source="google")
    assert image.url == "/api/images/google?photo=places%2FChIJabc_123-X%2Fphotos%2FAUy1YQ_photo-Token9&w=900"


def test_redact_secrets_masks_key_pairs():
    text = "GET https://serpapi.com/search.json?q=a&api_key=SECRET1&x=1 and ?key=SECRET2"
    masked = redact_secrets(text)
    assert "SECRET1" not in masked and "SECRET2" not in masked
    assert "q=a" in masked and "x=1" in masked
    assert scrub_text("plain text with no secret") == "plain text with no secret"


def test_log_records_are_masked(caplog):
    install_log_redaction()
    with caplog.at_level(logging.INFO):
        logging.getLogger("httpx").info("HTTP Request: GET %s", "https://x.test/a?key=TOPSECRET")
    assert "TOPSECRET" not in caplog.text


# -- (ii) the picture route -------------------------------------------------------


def _mock_upstream(monkeypatch, handler):
    calls: list[httpx.Request] = []

    def spy(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(misc, "_google_photo_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(spy)))
    misc._GOOGLE_PHOTO_CACHE.clear()
    return calls


def _client(tmp_path, **extra) -> TestClient:
    return TestClient(create_app(_settings(tmp_path, **extra)))


def _png(_request):
    return httpx.Response(200, content=b"\x89PNGdata", headers={"content-type": "image/png"})


def test_proxy_returns_image_bytes_and_keeps_the_key_in_a_header(tmp_path, monkeypatch):
    calls = _mock_upstream(monkeypatch, _png)
    with _client(tmp_path) as client:
        response = client.get("/api/images/google", params={"photo": PHOTO, "w": 900})
    assert response.status_code == 200
    assert response.content == b"\x89PNGdata"
    assert response.headers["content-type"] == "image/png"
    assert PLACES_KEY not in str(response.headers)
    assert len(calls) == 1
    assert calls[0].url.host == "places.googleapis.com"
    assert calls[0].headers["x-goog-api-key"] == PLACES_KEY
    assert "key=" not in str(calls[0].url)


def test_proxy_follows_the_google_image_redirect_without_the_key(tmp_path, monkeypatch):
    def handler(request):
        if request.url.host == "places.googleapis.com":
            return httpx.Response(302, headers={"location": "https://lh3.googleusercontent.com/p/abc"})
        return httpx.Response(200, content=b"JPEG", headers={"content-type": "image/jpeg"})

    calls = _mock_upstream(monkeypatch, handler)
    with _client(tmp_path) as client:
        response = client.get("/api/images/google", params={"photo": PHOTO})
    assert response.status_code == 200 and response.content == b"JPEG"
    assert "x-goog-api-key" not in calls[1].headers


def test_proxy_will_not_follow_a_redirect_to_another_host(tmp_path, monkeypatch):
    calls = _mock_upstream(monkeypatch, lambda r: httpx.Response(302, headers={"location": "http://169.254.169.254/x"}))
    with _client(tmp_path) as client:
        response = client.get("/api/images/google", params={"photo": PHOTO})
    assert response.status_code == 502
    assert len(calls) == 1
    assert PLACES_KEY not in response.text


@pytest.mark.parametrize(
    "photo",
    [
        "",
        "places/abc/photos/../../secret",
        "places/abc/photos/xyz/../../../v1/places:searchText",
        "../places/abc/photos/xyz",
        "https://evil.test/places/abc/photos/xyz",
        "places/abc/photos/xyz?key=1",
        "places/abc/photos/xyz\n",
        "places/abc/photos/",
        "places//photos/xyz",
        "places/abc/photos/x%2Fy",
    ],
)
def test_proxy_rejects_bad_photo_names(tmp_path, monkeypatch, photo):
    calls = _mock_upstream(monkeypatch, _png)
    with _client(tmp_path) as client:
        response = client.get("/api/images/google", params={"photo": photo})
    assert response.status_code == 404
    assert calls == []
    assert PLACES_KEY not in response.text


def test_proxy_clamps_the_width(tmp_path, monkeypatch):
    calls = _mock_upstream(monkeypatch, _png)
    with _client(tmp_path) as client:
        client.get("/api/images/google", params={"photo": PHOTO, "w": 999999})
        client.get("/api/images/google", params={"photo": PHOTO, "w": 3})
        client.get("/api/images/google", params={"photo": PHOTO, "w": "abc"})
    assert [c.url.params["maxWidthPx"] for c in calls] == ["1600", "100", "900"]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"<html>no</html>", headers={"content-type": "text/html"}),
        httpx.Response(200, content=b"<svg/>", headers={"content-type": "image/svg+xml"}),
        httpx.Response(200, content=b"x" * (5_000_001), headers={"content-type": "image/jpeg"}),
        httpx.Response(403, text=f"denied for key {PLACES_KEY}"),
    ],
)
def test_proxy_refuses_non_images_oversize_and_upstream_errors(tmp_path, monkeypatch, response):
    _mock_upstream(monkeypatch, lambda r: response)
    with _client(tmp_path) as client:
        result = client.get("/api/images/google", params={"photo": PHOTO})
    assert result.status_code == 502
    assert PLACES_KEY not in result.text and "googleapis" not in result.text


def test_proxy_upstream_timeout_is_a_clean_502(tmp_path, monkeypatch):
    def boom(request):
        raise httpx.ConnectTimeout(f"timed out {request.url}", request=request)

    _mock_upstream(monkeypatch, boom)
    with _client(tmp_path) as client:
        result = client.get("/api/images/google", params={"photo": PHOTO})
    assert result.status_code == 502
    assert PLACES_KEY not in result.text and "googleapis" not in result.text


def test_proxy_without_a_configured_key_is_404(tmp_path, monkeypatch):
    calls = _mock_upstream(monkeypatch, _png)
    with _client(tmp_path, google_places_api_key="") as client:
        assert client.get("/api/images/google", params={"photo": PHOTO}).status_code == 404
    assert calls == []


# -- (iii) old data -----------------------------------------------------------------


def _seed_old_rows(path: str, body: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO plans (session_id, version, body, created_at) VALUES ('s1', 1, ?, 'now')", (body,))
    conn.execute(
        "INSERT INTO wishlist (id, session_id, identity, body, created_at) VALUES ('w1','s1','me',?, 'now')", (body,)
    )
    meta = json.dumps({"image": KEYED_URL})
    conn.execute(
        "INSERT INTO messages (id, session_id, role, content, created_at, meta) "
        "VALUES ('m1','s1','assistant','hi','now',?)",
        (meta,),
    )
    conn.commit()
    conn.close()


_ALL_STORED = "SELECT body FROM plans UNION ALL SELECT body FROM wishlist UNION ALL SELECT meta FROM messages"


def test_store_init_scrubs_old_rows_and_is_idempotent(tmp_path):
    path = str(tmp_path / "old.db")
    Store(path).init()
    body = _old_body(tmp_path)
    assert PLACES_KEY in body
    _seed_old_rows(path, body)

    Store(path).init()
    conn = sqlite3.connect(path)
    rows = [r[0] for r in conn.execute(_ALL_STORED)]
    assert len(rows) == 3
    for text in rows:
        assert PLACES_KEY not in text and "key=" not in text
    assert "/api/images/google?photo=" in rows[0]
    Store(path).init()  # a second run changes nothing
    assert [r[0] for r in conn.execute(_ALL_STORED)] == rows
    conn.close()


def test_store_init_on_an_empty_database(tmp_path):
    store = Store(str(tmp_path / "empty.db"))
    store.init()
    store.init()


def test_read_path_scrubs_a_row_that_bypassed_the_migration(tmp_path):
    path = str(tmp_path / "late.db")
    store = Store(path)
    store.init()
    _seed_old_rows(path, _old_body(tmp_path))  # written after init: no migration ran on it

    assert PLACES_KEY not in json.dumps(asyncio.run(store.load_plan("s1")))
    assert PLACES_KEY not in json.dumps(asyncio.run(store.get_wishlist("w1")))
    assert PLACES_KEY not in json.dumps(asyncio.run(store.list_messages("s1")))


def test_journey_and_public_responses_scrub_a_stored_plan(tmp_path):
    with _client(tmp_path) as client:
        headers = {"X-Client-Token": "tester"}
        session_id = client.post("/api/journeys", json={}, headers=headers).json()["session"]["id"]
        body = json.loads(_old_body(tmp_path))
        body["session_id"] = session_id
        conn = sqlite3.connect(str(tmp_path / "leak.db"))
        conn.execute(
            "INSERT INTO plans (session_id, version, body, created_at) VALUES (?, 1, ?, 'now')",
            (session_id, json.dumps(body)),
        )
        conn.commit()
        conn.close()
        state = client.get(f"/api/journeys/{session_id}", headers=headers)
        assert state.status_code == 200 and state.json()["plan"]
        slug = client.post(f"/api/journeys/{session_id}/share", headers=headers).json()["slug"]
        public = client.get(f"/api/public/{slug}")
        assert public.status_code == 200
        for text in (state.text, public.text):
            assert PLACES_KEY not in text and "key=" not in text


# -- (iv) every configured key, every client-facing response ----------------------


def test_no_configured_key_appears_in_client_responses(tmp_path):
    with _client(tmp_path, google_maps_api_key=MAPS_KEY) as client:
        headers = {"X-Client-Token": "tester"}
        session_id = client.post("/api/journeys", json={}, headers=headers).json()["session"]["id"]
        plan = _plan(tmp_path)
        plan.session_id = session_id
        plan.version = 1
        conn = sqlite3.connect(str(tmp_path / "leak.db"))
        conn.execute(
            "INSERT INTO plans (session_id, version, body, created_at) VALUES (?, 1, ?, 'now')",
            (session_id, plan.model_dump_json()),
        )
        conn.commit()
        conn.close()
        slug = client.post(f"/api/journeys/{session_id}/share", headers=headers).json()["slug"]
        responses = [
            client.get(f"/api/journeys/{session_id}", headers=headers),
            client.get(f"/api/journeys/{session_id}/revisions", headers=headers),
            client.get(f"/api/public/{slug}"),
            client.get(f"/s/{slug}"),
            client.get("/api/runtime"),
            client.get("/api/ready"),
        ]
        raw_db = (tmp_path / "leak.db").read_bytes()
    for response in responses:
        assert response.status_code == 200, response.request.url
        for name, value in SENTINELS.items():
            assert value not in response.text, (name, response.request.url)
        assert "key=" not in response.text
    for value in SENTINELS.values():
        assert value.encode() not in raw_db


def test_the_places_key_is_not_offered_as_the_map_key(tmp_path):
    with _client(tmp_path, google_maps_api_key="") as client:
        maps = client.get("/api/runtime").json()["maps"]["google"]
    assert maps == {"key": "", "enabled": False}
