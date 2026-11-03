"""A map outage at the destination lookup ends the turn with fixed copy, and no source's words leave the server.

The real geocoder is a chain of sources. When every one of them fails, what the sources said is
for the operator's trace; the traveller is told a data source failed and the plan is left alone.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.errors import ProviderError
from app.domain.models import FailureKind
from app.infrastructure.providers.geocoding import GeocoderChain
from app.interface.api import create_app

SOURCE_FAILED = "A data source failed. Your plan was not changed."
WORDS = "UPSTREAM-PAGE <html>key=abc123</html>"
TURN = {"message": "Plan my trip.", "locale": "en", "client_token": "guest-outage"}


class _Failing:
    def __init__(self, name: str, kind: FailureKind, *, retryable: bool = True) -> None:
        self.name = name
        self.kind = kind
        self.retryable = retryable

    async def geocode(self, name: str, *, country_hint: str = "") -> Any:
        raise ProviderError(f"{self.name} failed", kind=self.kind, source=self.name, retryable=self.retryable, detail=WORDS)


def _frames(body: str) -> list[dict[str, Any]]:
    out = []
    for block in body.split("\n\n"):
        if block.startswith("event: error"):
            out.append(json.loads(block.split("data: ", 1)[1]))
    return out


def _turn(settings: Settings, chain: GeocoderChain) -> tuple[str, list[dict[str, Any]], list[str]]:
    guest = {"X-Client-Token": "guest-outage"}

    async def rates() -> None:
        return None

    with TestClient(create_app(settings)) as client:
        registry = client.app.state.container.registry
        registry.geocoder = chain
        registry.fx = SimpleNamespace(refresh=rates)  # no network for the exchange rates
        session_id = client.post("/api/journeys", json={"client_token": "guest-outage"}).json()["session"]["id"]
        client.patch(
            f"/api/journeys/{session_id}/state",
            json={
                "destination": "Kyoto",
                "origin": "Hong Kong",
                "start_date": "2027-04-10",
                "end_date": "2027-04-12",
                "party_size": 2,
                "budget": 400000,
                "currency": "JPY",
            },
            headers=guest,
        )
        body = client.post("/api/turn/stream", json={**TURN, "session_id": session_id}, headers=guest).text
        saved = client.get(f"/api/journeys/{session_id}", headers=guest).json()["messages"]
    return body, _frames(body), [m["content"] for m in saved if m["role"] == "assistant"]


def test_a_timeout_of_every_map_source_is_a_retryable_failed_data_source(settings: Settings) -> None:
    chain = GeocoderChain([_Failing("a", FailureKind.TIMEOUT), _Failing("b", FailureKind.NETWORK)])

    body, errors, assistant = _turn(settings, chain)

    assert len(errors) == 1
    assert errors[0]["message"] == SOURCE_FAILED
    assert (errors[0]["code"], errors[0]["kind"], errors[0]["retryable"]) == ("provider_error", "timeout", True)
    assert assistant == [SOURCE_FAILED]
    assert "UPSTREAM" not in body and "abc123" not in body and "<html>" not in body
    assert "Try the city and country together" not in body


def test_a_refusal_of_every_map_source_is_a_failed_data_source_that_asking_again_will_not_fix(settings: Settings) -> None:
    chain = GeocoderChain([_Failing("a", FailureKind.AUTH, retryable=False), _Failing("b", FailureKind.AUTH, retryable=False)])

    body, errors, assistant = _turn(settings, chain)

    assert len(errors) == 1
    assert (errors[0]["code"], errors[0]["kind"], errors[0]["retryable"]) == ("provider_error", "auth", False)
    assert "UPSTREAM" not in body and "abc123" not in body
