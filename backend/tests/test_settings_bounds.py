"""A retry or call budget below zero is refused at start instead of switching the data sources off.

``HTTP_RETRIES=-1`` used to leave the retry loop with no attempt at all, so every keyed data source
failed at once, and ``MAX_TOOL_CALLS_PER_TURN=-1`` refused every lookup of every turn.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.infrastructure.http import request_json, use_transport


@pytest.mark.parametrize("name", ["http_retries", "max_tool_calls_per_turn"])
def test_a_negative_budget_is_refused(name: str) -> None:
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None, **{name: -1})

    assert name in str(caught.value)


@pytest.mark.parametrize("name", ["http_retries", "max_tool_calls_per_turn"])
def test_a_negative_budget_in_the_environment_is_refused(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(name.upper(), "-3")

    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)

    assert name in str(caught.value)


@pytest.mark.parametrize("name", ["http_retries", "max_tool_calls_per_turn"])
def test_zero_is_still_a_valid_budget(name: str) -> None:
    assert getattr(Settings(_env_file=None, **{name: 0}), name) == 0


def test_a_request_with_a_negative_retry_count_is_still_made_once() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    with use_transport(httpx.MockTransport(handler)):
        data = asyncio.run(request_json("GET", "https://provider.example/api", source="demo", retries=-1))

    assert data == {"ok": True} and len(requests) == 1
