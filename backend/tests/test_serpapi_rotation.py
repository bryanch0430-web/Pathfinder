"""SerpAPI key rotation: one key is in use, the next ready key takes over, the others stay listed."""

from __future__ import annotations

import asyncio

import httpx

from app.config import Settings
from app.infrastructure.http import use_transport
from app.infrastructure.secret_box import SecretBox
from app.infrastructure.serpapi_keys import bind_ring, install_ring, parse_pool, quota_failure, serpapi_json
from app.infrastructure.store import SQLiteStore


def test_the_pool_keeps_one_key_in_use_and_the_rest_waiting() -> None:
    rows = parse_pool(
        "active|New|aaaaaaa1;ready|Next|bbbbbbb2;paused|Later|ccccccc3;exhausted|Used up|ddddddd4;active|Extra|eeeeeee5"
    )
    assert [row[0] for row in rows] == ["active", "ready", "paused", "exhausted", "ready"]
    assert rows[1][1] == "Next"


def test_a_used_up_key_hands_the_search_to_the_next_ready_one(settings: Settings, tmp_path) -> None:
    settings.serpapi_key = ""
    settings.serpapi_key_pool = "active|New|activekey1;ready|Next|readykey02;paused|Later|pausedkey3;exhausted|Used|usedkey004"
    store = SQLiteStore(tmp_path / "serp.db")
    box = SecretBox(settings)
    ring = install_ring(store, box, settings)
    try:
        assert settings.serpapi_key == "activekey1"
        assert [row["status"] for row in store.serpapi_keys()] == ["active", "ready", "paused", "exhausted"]
        assert quota_failure("Your account has run out of searches.")
        calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.params["api_key"])
            if request.url.params["api_key"] == "activekey1":
                return httpx.Response(200, json={"error": "Your account has run out of searches."})
            return httpx.Response(200, json={"properties": [{"name": "Inn"}]})

        data = asyncio.run(
            _search(settings, handler)
        )
        assert calls == ["activekey1", "readykey02"]
        assert data["properties"][0]["name"] == "Inn"
        assert settings.serpapi_key == "readykey02"
        statuses = {row["key_tail"]: row["status"] for row in store.serpapi_keys()}
        assert statuses["key1"] == "exhausted"
        assert statuses["ey02"] == "active"
        assert statuses["key3"] == "paused"
        assert statuses["y004"] == "exhausted"
        assert ring.current() == "readykey02"
    finally:
        bind_ring(None)


async def _search(settings: Settings, handler) -> dict:
    with use_transport(httpx.MockTransport(handler)):
        return await serpapi_json(
            settings,
            source="serpapi-hotels",
            params={"engine": "google_hotels", "q": "osaka"},
            timeout=5,
            retries=0,
            user_agent="test",
        )
