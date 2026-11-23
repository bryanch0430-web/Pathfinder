from __future__ import annotations

import asyncio

from app.config import Settings
from app.core.trace import TurnTrace
from app.evidence.providers import Providers


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, deepseek_results=None):
        self.deepseek_results = deepseek_results or []
        self.posts = []
        self.gets = []

    async def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        return FakeResponse({"query": {"search": []}})

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return FakeResponse({"results": self.deepseek_results})


def test_search_uses_deepseek_only_after_serpapi_and_wikipedia_are_empty():
    client = FakeClient(
        [
            {
                "title": "Victoria Peak",
                "url": "https://example.com/peak",
                "summary": "The Peak is open late.",
            }
        ]
    )
    settings = Settings(
        _env_file=None,
        SERPAPI_KEY="",
        DEEPSEEK_SEARCH_API_KEY="test-search-key",
        WEB_SEARCH_ENABLED=True,
    )
    trace = TurnTrace(request_id="search-fallback")
    providers = Providers(settings, trace=trace, client=client)
    rows = asyncio.run(providers.search("Peak opening hours", limit=3))

    assert rows == [
        {
            "title": "Victoria Peak",
            "url": "https://example.com/peak",
            "snippet": "The Peak is open late.",
        }
    ]
    assert client.posts and client.posts[0][0].endswith("/v1/tools/search")
    assert client.posts[0][1]["headers"]["Authorization"] == "Bearer test-search-key"
    assert [row["provider"] for row in trace.searches] == ["Wikipedia", "DeepSeek Search"]


def test_search_does_not_call_deepseek_when_wikipedia_has_results():
    class WikiClient(FakeClient):
        async def get(self, url, **kwargs):
            self.gets.append((url, kwargs))
            return FakeResponse(
                {"query": {"search": [{"title": "Peak", "snippet": "A hill in Hong Kong."}]}}
            )

    client = WikiClient()
    settings = Settings(
        _env_file=None,
        SERPAPI_KEY="",
        DEEPSEEK_SEARCH_API_KEY="test-search-key",
        WEB_SEARCH_ENABLED=True,
    )
    trace = TurnTrace(request_id="search-wiki")
    providers = Providers(settings, trace=trace, client=client)
    rows = asyncio.run(providers.search("Peak", limit=3))

    assert rows[0]["title"] == "Peak"
    assert client.posts == []
    assert [row["provider"] for row in trace.searches] == ["Wikipedia"]
