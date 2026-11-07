"""Each HTTP request is bound to its caller, whose own keys pay for the writer and for Grok search."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.application.auth import create_token
from app.application.llm_selection import bind_caller, current_owner
from app.application.orchestrator import KEY_FAILED, SOURCE_FAILED, user_facing_error
from app.application.web_lookup import web_sources
from app.config import Settings
from app.domain.credentials import SERVER_OWNER
from app.domain.errors import CallerKeyFailed, ProviderError, ProviderNoData
from app.domain.models import GeoLocation, GeoPoint
from app.infrastructure.http import override_default_transport, use_transport
from app.infrastructure.providers.registry import ProviderRegistry
from app.infrastructure.providers.websearch import GrokSearchSource
from app.infrastructure.secret_box import SecretBox, mask_key
from app.interface.api import create_app

ANTHROPIC_KEY = "sk-ant-guest-a-0000000000aaaa"
ENV_BASE = "https://env-writer.test/v1"
CALLER_XAI = "xai-guest-a-4444444444dddd"
OPERATOR_XAI = "xai-operator-5555555555eeee"
ANTHROPIC_OK = {"model": "m", "content": [{"type": "text", "text": "{}"}], "usage": {}}
OPENAI_OK = {"model": "m", "choices": [{"message": {"content": "{}"}}], "usage": {}}
GROK_OK = {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Kyoto is lovely."}]}]}
TRIP = {
    "destination": "Kyoto",
    "origin": "Hong Kong",
    "start_date": "2027-04-10",
    "end_date": "2027-04-12",
    "party_size": 2,
    "budget": 400000,
    "currency": "JPY",
}


class Recorder:
    """Answers every host and keeps the host, path and key of each request."""

    def __init__(self, status: dict[str, int] | None = None) -> None:
        self.calls: list[dict[str, str]] = []
        self.status = status or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        key = request.headers.get("x-api-key") or request.headers.get("authorization", "").removeprefix("Bearer ")
        self.calls.append({"host": request.url.host, "path": request.url.path, "key": key})
        status = self.status.get(request.url.host, 200)
        if status >= 400:
            # The provider echoes the key back; it must never reach our error text.
            return httpx.Response(status, json={"error": {"message": f"Incorrect API key provided: {key}"}})
        if request.url.host == "api.anthropic.com":
            return httpx.Response(200, json=ANTHROPIC_OK)
        if request.url.host in {"env-writer.test", "api.openai.com"}:
            return httpx.Response(200, json=OPENAI_OK)
        if request.url.host == "api.x.ai":
            return httpx.Response(200, json=GROK_OK)
        return httpx.Response(404, json={})

    def to(self, host: str) -> list[dict[str, str]]:
        return [call for call in self.calls if call["host"] == host]


class Transported:
    """Test only: routes every request the app makes through ``transport``, in the app's own thread."""

    def __init__(self, app: Any, transport: httpx.AsyncBaseTransport) -> None:
        self.app = app
        self.transport = transport

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        with use_transport(self.transport):
            await self.app(scope, receive, send)


def _owner_app(settings: Settings) -> FastAPI:
    app = create_app(settings)

    async def owner() -> dict[str, Any]:
        return {"owner": current_owner()}

    app.add_api_route("/api/test-owner", owner, methods=["GET"])
    app.router.routes.insert(0, app.router.routes.pop())  # ahead of any catch-all route
    return app


def _owner(settings: Settings, headers: dict[str, str]) -> Any:
    with TestClient(_owner_app(settings)) as client:
        response = client.get("/api/test-owner", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["owner"]


def test_header_binds_guest_owner(settings: Settings) -> None:
    assert _owner(settings, {"X-Client-Token": "guest-a"}) == "guest-a"


def test_bearer_binds_user_owner(settings: Settings) -> None:
    token = create_token("u123", "a@example.com", settings.auth_secret)
    headers = {"Authorization": f"Bearer {token}", "X-Client-Token": "guest-a"}
    assert _owner(settings, headers) == "user:u123"


def test_no_identity_binds_none(settings: Settings) -> None:
    assert _owner(settings, {}) is None


def test_server_token_and_unsigned_user_token_bind_none(settings: Settings) -> None:
    # "server" is the operator's own row; a "user:" token is only ever the bearer's.
    assert _owner(settings, {"X-Client-Token": SERVER_OWNER}) is None
    assert _owner(settings, {"X-Client-Token": "user:u123"}) is None
    assert _owner(settings, {"X-Client-Token": "has space"}) is None
    forged = create_token("u123", "a@example.com", "not-the-server-secret-" + "x" * 32)
    assert _owner(settings, {"Authorization": f"Bearer {forged}", "X-Client-Token": "guest-a"}) == "guest-a"


def _stream_turn(
    settings: Settings,
    recorder: Recorder,
    message: str = "Plan my trip.",
    trip: dict[str, Any] | None = None,
    planned_first: bool = False,
) -> str:
    """One streamed turn. ``planned_first`` plans the trip first, with every host answering 200."""
    guest = {"X-Client-Token": "guest-a"}

    async def geocode(name: str, *, country_hint: str = "") -> GeoLocation | None:
        return GeoLocation(name=name, geo=GeoPoint(lat=35.0116, lng=135.7681), source="fake-geocoder")

    async def rates() -> None:
        return None

    transport = httpx.MockTransport(recorder)
    app = create_app(settings)
    app.add_middleware(Transported, transport=transport)
    with override_default_transport(transport), TestClient(app) as client:
        container = client.app.state.container
        container.store.add_credential(
            "guest-a", "anthropic", "", container.box.encrypt(ANTHROPIC_KEY), mask_key(ANTHROPIC_KEY), False, True, ""
        )
        container.registry.geocoder = SimpleNamespace(name="fake-geocoder", geocode=geocode)
        container.registry.fx = SimpleNamespace(refresh=rates)
        session_id = client.post("/api/journeys", json={}, headers=guest).json()["session"]["id"]
        client.patch(f"/api/journeys/{session_id}/state", json=TRIP if trip is None else trip, headers=guest)
        if planned_first:
            failing = recorder.status
            recorder.status = {}
            client.post(
                "/api/turn/stream",
                json={"message": "Plan my trip.", "locale": "en", "session_id": session_id},
                headers=guest,
            )
            recorder.status = failing
            recorder.calls.clear()
        return client.post(
            "/api/turn/stream",
            json={"message": message, "locale": "en", "session_id": session_id},
            headers=guest,
        ).text


def _with_env(settings: Settings) -> Settings:
    return settings.model_copy(update={"llm_api_key": "env-key-3333333333", "llm_base_url": ENV_BASE, "llm_model": "env-model"})


def test_stream_turn_uses_caller_key(settings: Settings) -> None:
    recorder = Recorder()
    _stream_turn(_with_env(settings), recorder)

    messages = [call for call in recorder.to("api.anthropic.com") if call["path"].endswith("/messages")]
    assert messages, recorder.calls
    assert all(call["key"] == ANTHROPIC_KEY for call in messages)
    assert recorder.to("env-writer.test") == []


def test_caller_key_failure_streams_llm_key_event(settings: Settings) -> None:
    recorder = Recorder(status={"api.anthropic.com": 401})
    body = _stream_turn(_with_env(settings), recorder)

    frames = [block for block in body.split("\n\n") if block.startswith("event: llm_key")]
    assert frames, body[-2000:]
    data = json.loads(frames[0].split("data: ", 1)[1])
    assert data["provider"] == "anthropic" and data["reason"] == "auth"
    assert recorder.to("api.anthropic.com")
    assert recorder.to("env-writer.test") == []
    assert ANTHROPIC_KEY not in body


def _frames(body: str, event: str) -> list[dict[str, Any]]:
    return [
        json.loads(block.split("data: ", 1)[1])
        for block in body.split("\n\n")
        if block.startswith(f"event: {event}\n")
    ]


def _events(body: str) -> list[str]:
    return [block.split("\n", 1)[0].removeprefix("event: ") for block in body.split("\n\n") if block.startswith("event: ")]


def test_ask_turn_tells_a_failed_caller_key_once(settings: Settings) -> None:
    # The ask path never listens for live events, so the stream itself tells the traveller.
    recorder = Recorder(status={"api.anthropic.com": 401})
    body = _stream_turn(_with_env(settings), recorder, message="What is the weather on day 2?", planned_first=True)

    notices = _frames(body, "llm_key")
    assert notices == [{"provider": "anthropic", "model": notices[0]["model"], "reason": "auth"}], body[-2000:]
    assert _frames(body, "route")[0]["intent"] == "ask"
    assert recorder.to("api.anthropic.com")
    assert recorder.to("env-writer.test") == []
    assert ANTHROPIC_KEY not in body
    # The turn completes on the rule-based answer: the notice comes before the last frame, and no error is shown.
    events = _events(body)
    assert events[-1] == "done" and events.index("llm_key") < events.index("done")
    assert _frames(body, "error") == []
    assert "".join(frame["text"] for frame in _frames(body, "token")).strip()


def test_plan_turn_with_missing_slots_tells_an_understand_failure_once(settings: Settings) -> None:
    # The trip lacks its dates and party, so understand calls the writer first, before any live listener.
    recorder = Recorder(status={"api.anthropic.com": 401})
    body = _stream_turn(_with_env(settings), recorder, message="Plan a trip to Kyoto.", trip={})

    understand = [call for call in recorder.to("api.anthropic.com") if call["path"].endswith("/messages")]
    assert understand, recorder.calls
    notices = _frames(body, "llm_key")
    assert len(notices) == 1, body[-2000:]
    assert notices[0]["provider"] == "anthropic" and notices[0]["reason"] == "auth"
    assert recorder.to("env-writer.test") == []
    assert ANTHROPIC_KEY not in body
    errors = _frames(body, "error")
    if errors:
        assert errors[-1]["message"] == KEY_FAILED
    else:
        assert _events(body)[-1] == "done"


def _seed_xai(settings: Settings, box: SecretBox | None = None):
    from app.container import build_container

    container = build_container(settings)
    box = box or container.box
    container.store.add_credential(
        "guest-a", "xai", "grok-caller", box.encrypt(CALLER_XAI), mask_key(CALLER_XAI), True, True, ""
    )
    return container


def _answer(source: GrokSearchSource, owner: str | None, recorder: Recorder) -> tuple[bool, Any]:
    async def scenario() -> tuple[bool, Any]:
        with use_transport(httpx.MockTransport(recorder)), bind_caller(owner):
            available = source.available
            try:
                return available, await source.answer(query="What is Kyoto like?")
            except ProviderError as exc:
                return available, exc

    return asyncio.run(scenario())


def test_grok_search_uses_caller_xai_key(settings: Settings) -> None:
    container = _seed_xai(settings.model_copy(update={"grok_search_enabled": False}))
    source = container.registry.web_deep[0]
    assert isinstance(source, GrokSearchSource)
    assert source.available is False  # no caller bound, and the operator has not enabled Grok search

    recorder = Recorder()
    available, answer = _answer(source, "guest-a", recorder)

    assert available is True
    assert answer.text == "Kyoto is lovely."
    sent = recorder.to("api.x.ai")
    assert [(call["path"], call["key"]) for call in sent] == [("/v1/responses", CALLER_XAI)]


def test_grok_search_caller_key_never_goes_to_the_operator_base_url(settings: Settings) -> None:
    relayed = settings.model_copy(
        update={
            "xai_base_url": "https://relay.example/v1",
            "grok_search_enabled": True,
            "xai_api_key": OPERATOR_XAI,
            "xai_model": "grok-operator",
        }
    )
    container = _seed_xai(relayed)
    source = container.registry.web_deep[0]

    caller_recorder = Recorder()
    _, answer = _answer(source, "guest-a", caller_recorder)
    assert answer.text == "Kyoto is lovely."
    assert [(call["host"], call["path"], call["key"]) for call in caller_recorder.calls] == [
        ("api.x.ai", "/v1/responses", CALLER_XAI)
    ]
    assert caller_recorder.to("relay.example") == []

    # The operator's own key still goes where the operator pointed it.
    operator_recorder = Recorder()
    _answer(source, "guest-b", operator_recorder)
    assert [(call["host"], call["key"]) for call in operator_recorder.calls] == [("relay.example", OPERATOR_XAI)]


def test_grok_search_caller_key_error_is_scrubbed(settings: Settings) -> None:
    container = _seed_xai(settings)
    source = container.registry.web_deep[0]
    _, failure = _answer(source, "guest-a", Recorder(status={"api.x.ai": 401}))

    assert isinstance(failure, ProviderError)
    assert CALLER_XAI not in str(failure) and CALLER_XAI not in failure.detail


def test_grok_search_operator_key_without_caller_row(settings: Settings) -> None:
    operator = settings.model_copy(
        update={"grok_search_enabled": True, "xai_api_key": OPERATOR_XAI, "xai_model": "grok-operator"}
    )
    container = _seed_xai(operator)
    source = container.registry.web_deep[0]

    recorder = Recorder()
    available, answer = _answer(source, "guest-b", recorder)

    assert available is True and answer.text == "Kyoto is lovely."
    assert [call["key"] for call in recorder.to("api.x.ai")] == [OPERATOR_XAI]


def test_grok_search_unreadable_caller_key_does_not_use_operator_key(settings: Settings) -> None:
    operator = settings.model_copy(
        update={"grok_search_enabled": True, "xai_api_key": OPERATOR_XAI, "xai_model": "grok-operator"}
    )
    other_box = SecretBox(settings.model_copy(update={"auth_secret": "a-different-secret-" + "x" * 32}))
    container = _seed_xai(operator, box=other_box)
    source = container.registry.web_deep[0]

    recorder = Recorder()
    available, failure = _answer(source, "guest-a", recorder)

    assert available is False
    assert isinstance(failure, ProviderNoData)
    assert str(failure) == "Your saved xAI key can no longer be read. Remove it and add it again in Settings."
    assert recorder.calls == []


def test_registry_without_selector_uses_operator_keys_only(settings: Settings) -> None:
    container = _seed_xai(settings)  # guest-a has a readable xAI search row
    with bind_caller("guest-a"):
        assert container.registry.web_deep[0].available is True  # the container's registry has the selector
    registry = ProviderRegistry.build(settings)
    assert [source.name for source in registry.web_deep] == ["grok-web-search"]
    assert registry.web_deep[0].selector is None
    with bind_caller("guest-a"):
        assert registry.web_deep[0].available is False


def test_reference_lookup_keeps_the_operator_xai_key_when_grok_search_is_off(settings: Settings) -> None:
    """The reference fallback has always used XAI_API_KEY without GROK_SEARCH_ENABLED."""
    operator = settings.model_copy(update={"xai_api_key": OPERATOR_XAI, "xai_model": "grok-operator"})
    container = _seed_xai(operator)
    with bind_caller("guest-b"):
        _, grok = web_sources(container.registry, operator, None)
        assert grok is not None and grok.available
    with bind_caller("guest-a"):
        _, grok = web_sources(container.registry, operator, None)
        recorder = Recorder()

        async def call() -> Any:
            with use_transport(httpx.MockTransport(recorder)):
                return await grok.answer(query="Kyoto")

        asyncio.run(call())
    assert [call["key"] for call in recorder.to("api.x.ai")] == [CALLER_XAI]


def test_user_facing_error_names_key() -> None:
    failure = CallerKeyFailed("Your Anthropic key failed: the provider refused it. Check the key in Settings.")
    assert user_facing_error(failure) == KEY_FAILED
    assert KEY_FAILED == (
        "Your own API key failed, so this answer could not use your model. Check or replace the key in Settings."
    )
    assert user_facing_error(ProviderError("a source failed")) == SOURCE_FAILED
