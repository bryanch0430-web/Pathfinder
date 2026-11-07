"""Per-request writer selection: the caller's own key, else the operator chain."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.application.llm_selection import (
    CallerLLM,
    LLMSelector,
    LocalProbe,
    SearchKey,
    bind_caller,
    current_caller_failure,
    current_owner,
    current_search_key,
    take_key_notice,
)
from app.config import Settings
from app.container import build_container
from app.domain.credentials import SERVER_OWNER
from app.domain.errors import CallerKeyFailed, ProviderError
from app.infrastructure.http import use_transport
from app.infrastructure.observability import bind_live_emit, reset_live_emit
from app.infrastructure.secret_box import SecretBox, mask_key
from app.infrastructure.store import SQLiteStore

OPENAI_OK = {"model": "m", "choices": [{"message": {"content": "{}"}}], "usage": {}}
ANTHROPIC_OK = {"model": "m", "content": [{"type": "text", "text": "{}"}], "usage": {}}

ENV_BASE = "https://env-writer.test/v1"
LOCAL_BASE = "http://local-model.test/v1"
KEY_A = "sk-ant-guest-a-0000000000aaaa"
KEY_B = "sk-guest-b-1111111111bbbb"
SERVER_KEY = "sk-server-2222222222cccc"


def _with_env(settings: Settings) -> Settings:
    return settings.model_copy(update={"llm_api_key": "env-key-3333333333", "llm_base_url": ENV_BASE, "llm_model": "env-model"})


def _with_local(settings: Settings) -> Settings:
    return settings.model_copy(update={"local_llm_base_url": LOCAL_BASE, "local_llm_model": "llama-local"})


def _save(store: SQLiteStore, box: SecretBox, owner: str, provider: str, key: str, model: str = "",
          search: bool = False):
    return store.add_credential(owner, provider, model, box.encrypt(key), mask_key(key), search, True, "")


def _selector(settings: Settings, store: SQLiteStore, box: SecretBox, reachable: bool = True) -> LLMSelector:
    return LLMSelector(settings, store, box, probe=LocalProbe(settings, check=lambda: reachable))


class Recorder:
    """A mock transport that answers every provider and keeps what each request carried."""

    def __init__(self, status: dict[str, int] | None = None) -> None:
        self.calls: list[dict[str, str]] = []
        self.status = status or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        messages = body.get("messages") or [{}]
        self.calls.append({
            "host": request.url.host,
            "key": request.headers.get("x-api-key") or request.headers.get("authorization", "").removeprefix("Bearer "),
            "user": str(messages[-1].get("content", "")),
        })
        status = self.status.get(request.url.host, 200)
        if status >= 400:
            # The provider echoes the key back, as some do; it must never reach our error text.
            sent = self.calls[-1]["key"]
            return httpx.Response(status, json={"error": {"message": f"Incorrect API key provided: {sent}"}})
        return httpx.Response(200, json=ANTHROPIC_OK if request.url.host == "api.anthropic.com" else OPENAI_OK)

    def hosts(self) -> list[str]:
        return [call["host"] for call in self.calls]


async def _complete(llm: CallerLLM, user: str = "hi") -> Any:
    return await llm.complete(system="s", user=user, purpose="test")


@pytest.fixture()
def store(settings: Settings) -> SQLiteStore:
    return SQLiteStore(settings.database_path)


@pytest.fixture()
def box(settings: Settings) -> SecretBox:
    return SecretBox(settings)


def test_order_caller_then_server_then_env_then_local_then_null(settings: Settings, store: SQLiteStore,
                                                              box: SecretBox) -> None:
    assert _selector(settings, store, box).for_caller("guest-a").source == "none"
    local = _with_local(settings)
    assert _selector(local, store, box, reachable=False).for_caller("guest-a").source == "none"
    chosen = _selector(local, store, box).for_caller("guest-a")
    assert (chosen.source, chosen.provider, chosen.model) == ("local", "local", "llama-local")
    env = _with_env(local)
    chosen = _selector(env, store, box).for_caller("guest-a")
    assert (chosen.source, chosen.model) == ("env", "env-model")
    server_row = _save(store, box, SERVER_OWNER, "openai", SERVER_KEY, "gpt-server")
    chosen = _selector(env, store, box).for_caller("guest-a")
    assert (chosen.source, chosen.provider, chosen.model, chosen.credential_id) == (
        "server", "openai", "gpt-server", server_row.id)
    own = _save(store, box, "guest-a", "anthropic", KEY_A)
    chosen = _selector(env, store, box).for_caller("guest-a")
    assert (chosen.source, chosen.provider, chosen.credential_id, chosen.problem) == ("byok", "anthropic", own.id, "")
    assert chosen.model == "claude-sonnet-5-5"  # an empty saved model means the provider's default
    # Another caller, and no caller at all, still get the operator chain.
    assert _selector(env, store, box).for_caller("guest-b").source == "server"
    assert _selector(env, store, box).for_caller(None).source == "server"


def test_no_binding_uses_operator_chain(settings: Settings, store: SQLiteStore, box: SecretBox) -> None:
    env = _with_env(settings)
    _save(store, box, "guest-a", "openai", KEY_B)
    llm = CallerLLM(_selector(env, store, box))
    recorder = Recorder()

    async def scenario() -> None:
        with use_transport(httpx.MockTransport(recorder)):
            assert current_owner() is None
            assert llm.available is True and llm.name == "env-model"
            await _complete(llm)
            with bind_caller(None):
                await _complete(llm)

    asyncio.run(scenario())
    assert recorder.hosts() == ["env-writer.test", "env-writer.test"]
    assert all(call["key"] == "env-key-3333333333" for call in recorder.calls)


def test_two_callers_concurrently_use_own_keys(settings: Settings, store: SQLiteStore, box: SecretBox) -> None:
    env = _with_env(settings)
    _save(store, box, "guest-a", "anthropic", KEY_A)
    _save(store, box, "guest-b", "openai", KEY_B)
    selector = _selector(env, store, box)
    llm = CallerLLM(selector)
    recorder = Recorder()
    reads: list[str] = []
    active = store.active_credential

    def counting(owner: str):
        reads.append(owner)
        return active(owner)

    store.active_credential = counting  # type: ignore[method-assign]

    async def caller(owner: str) -> list[str]:
        with bind_caller(owner):
            assert current_owner() == owner
            names = []
            for turn in range(4):
                names.append(llm.name)
                await _complete(llm, user=f"{owner}:{turn}")
                await asyncio.sleep(0)
                # A child task copies the binding, and with it the memoised selection.
                await asyncio.create_task(_complete(llm, user=f"{owner}:child{turn}"))
            return names

    async def scenario() -> tuple[list[str], list[str]]:
        with use_transport(httpx.MockTransport(recorder)):
            return await asyncio.gather(caller("guest-a"), caller("guest-b"))

    names_a, names_b = asyncio.run(scenario())
    assert set(names_a) == {"claude-sonnet-5-5"} and set(names_b) == {"gpt-4o-mini"}
    by_owner = {"guest-a": set(), "guest-b": set()}
    for call in recorder.calls:
        by_owner[call["user"].split(":")[0]].add((call["host"], call["key"]))
    assert by_owner["guest-a"] == {("api.anthropic.com", KEY_A)}
    assert by_owner["guest-b"] == {("api.openai.com", KEY_B)}
    assert len(recorder.calls) == 16
    # Resolved once per request, on first use, not once per call.
    assert sorted(reads) == ["guest-a", "guest-b"]


def test_failing_caller_key_does_not_fall_back(settings: Settings, store: SQLiteStore, box: SecretBox) -> None:
    configured = _with_env(_with_local(settings))
    _save(store, box, "guest-a", "openai", KEY_B, "gpt-4o-mini")
    probes: list[int] = []
    selector = LLMSelector(configured, store, box,
                           probe=LocalProbe(configured, check=lambda: probes.append(1) or True))
    llm = CallerLLM(selector)
    recorder = Recorder(status={"api.openai.com": 401})
    events: list[tuple[str, dict[str, Any]]] = []

    async def scenario() -> tuple[CallerKeyFailed, CallerKeyFailed]:
        token = bind_live_emit(lambda event, data: events.append((event, data)))
        try:
            with use_transport(httpx.MockTransport(recorder)), bind_caller("guest-a"):
                with pytest.raises(CallerKeyFailed) as first:
                    await _complete(llm)
                calls_after_first = len(recorder.calls)
                with pytest.raises(CallerKeyFailed) as second:
                    await _complete(llm)
                assert len(recorder.calls) == calls_after_first
                return first.value, second.value
        finally:
            reset_live_emit(token)

    first, second = asyncio.run(scenario())
    assert first is second
    assert isinstance(first, ProviderError)
    assert str(first).startswith("Your OpenAI key failed: ")
    assert first.kind.value == "auth" and first.source == "llm"
    assert KEY_B not in str(first) and KEY_B not in first.detail and KEY_B not in repr(first.as_dict())
    assert "[redacted]" in first.detail
    assert recorder.hosts() == ["api.openai.com"]
    assert probes == []
    assert events == [("llm_key", {"provider": "openai", "model": "gpt-4o-mini", "reason": "auth"})]


def test_unreadable_chosen_key_fails_without_fallback(settings: Settings, store: SQLiteStore,
                                                     box: SecretBox) -> None:
    configured = _with_env(_with_local(settings))
    other_box = SecretBox(settings.model_copy(update={"auth_secret": "a-different-secret-" + "x" * 32}))
    row = _save(store, other_box, "guest-a", "openai", KEY_B, "gpt-4o-mini")
    selector = _selector(configured, store, box)
    chosen = selector.for_caller("guest-a")
    assert (chosen.source, chosen.credential_id, chosen.problem) == ("byok", row.id, "unreadable")
    llm = CallerLLM(selector)
    recorder = Recorder()
    events: list[tuple[str, dict[str, Any]]] = []

    async def scenario() -> CallerKeyFailed:
        token = bind_live_emit(lambda event, data: events.append((event, data)))
        try:
            with use_transport(httpx.MockTransport(recorder)), bind_caller("guest-a"):
                assert llm.available is True
                with pytest.raises(CallerKeyFailed) as caught:
                    await _complete(llm)
                with pytest.raises(CallerKeyFailed):
                    await _complete(llm)
                return caught.value
        finally:
            reset_live_emit(token)

    failure = asyncio.run(scenario())
    assert str(failure) == "Your saved key can no longer be read. Remove it and add it again in Settings."
    assert recorder.calls == []
    assert [event for event, _ in events] == ["llm_key"]


def test_local_probe_failure_skips_local(settings: Settings, store: SQLiteStore, box: SecretBox) -> None:
    local = _with_local(settings)
    now = [100.0]
    probed: list[str] = []

    def answer(status: int | None):
        def handler(request: httpx.Request) -> httpx.Response:
            probed.append(str(request.url))
            if status is None:
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(status, json={"data": []})
        return handler

    def run(handler, probe: LocalProbe) -> str:
        with use_transport(httpx.MockTransport(handler)):
            return LLMSelector(local, store, box, probe=probe).for_caller("guest-a").source

    for failing in (500, None):
        probed.clear()
        probe = LocalProbe(local, ttl_seconds=60.0, clock=lambda: now[0])
        assert run(answer(failing), probe) == "none"
        now[0] += 30.0
        assert run(answer(failing), probe) == "none"
        assert probed == [f"{LOCAL_BASE}/models"]  # not repeated inside the TTL
        now[0] += 31.0
        assert run(answer(200), probe) == "local"
        assert len(probed) == 2

    # Not configured: no probe at all, and never an exception.
    probed.clear()
    assert LocalProbe(settings, check=lambda: probed.append("x") or True).reachable() is False
    assert probed == []
    assert LocalProbe(local, check=lambda: 1 / 0).reachable() is False


def test_health_reports_source(settings: Settings) -> None:
    container = build_container(settings)
    assert container.health()["writer"] == {"available": False, "model": "offline", "source": "none"}
    assert isinstance(container.llm, CallerLLM)
    assert isinstance(container.selector, LLMSelector) and isinstance(container.box, SecretBox)


def test_search_key_from_xai_row(settings: Settings, store: SQLiteStore, box: SecretBox) -> None:
    xai_key = "xai-guest-a-4444444444dddd"
    _save(store, box, "guest-a", "xai", xai_key, "grok-x", search=True)
    _save(store, box, "guest-a", "openai", KEY_B)  # now active; the xAI key still serves search
    _save(store, box, "guest-b", "xai", "xai-guest-b-5555555555eeee", search=False)
    selector = _selector(settings, store, box)
    assert selector.search_key("guest-a") == SearchKey(api_key=xai_key, model="grok-x")
    assert selector.search_key("guest-b") is None
    assert selector.search_key(None) is None
    assert current_search_key(selector) is None
    reads: list[str] = []
    search = store.search_credential

    def counting(owner: str):
        reads.append(owner)
        return search(owner)

    store.search_credential = counting  # type: ignore[method-assign]
    with bind_caller("guest-a"):
        assert current_search_key(selector) == SearchKey(api_key=xai_key, model="grok-x")
        assert current_search_key(selector) == SearchKey(api_key=xai_key, model="grok-x")
    assert reads == ["guest-a"]
    assert xai_key not in repr(SearchKey(api_key=xai_key, model="grok-x"))


def test_adapter_is_reused_until_the_row_changes(settings: Settings, store: SQLiteStore, box: SecretBox) -> None:
    row = _save(store, box, "guest-a", "openai", KEY_B, "gpt-4o-mini")
    selector = _selector(settings, store, box)
    first = selector.for_caller("guest-a").llm
    assert selector.for_caller("guest-a").llm is first
    store.update_credential("guest-a", row.id, model="gpt-4.1")
    changed = selector.for_caller("guest-a")
    assert changed.llm is not first and changed.model == "gpt-4.1" and changed.llm.name == "gpt-4.1"


def test_two_concurrent_failing_calls_tell_the_traveller_once(settings: Settings, store: SQLiteStore,
                                                              box: SecretBox) -> None:
    _save(store, box, "guest-a", "openai", KEY_B, "gpt-4o-mini")
    llm = CallerLLM(_selector(_with_env(settings), store, box))
    recorder = Recorder(status={"api.openai.com": 401})
    events: list[tuple[str, dict[str, Any]]] = []

    async def scenario() -> list[Any]:
        token = bind_live_emit(lambda event, data: events.append((event, data)))
        try:
            with use_transport(httpx.MockTransport(recorder)), bind_caller("guest-a"):
                outcomes = await asyncio.gather(_complete(llm, "one"), _complete(llm, "two"), return_exceptions=True)
                assert take_key_notice() is None  # already told live
                return outcomes
        finally:
            reset_live_emit(token)

    outcomes = asyncio.run(scenario())
    assert all(isinstance(outcome, CallerKeyFailed) for outcome in outcomes)
    assert outcomes[0] is outcomes[1]
    assert events == [("llm_key", {"provider": "openai", "model": "gpt-4o-mini", "reason": "auth"})]
    assert "env-writer.test" not in recorder.hosts()


def test_failure_without_a_listener_is_told_later_once(settings: Settings, store: SQLiteStore,
                                                       box: SecretBox) -> None:
    """A failure before the turn listens (understand, the router) is told by the next call that has a listener."""
    _save(store, box, "guest-a", "openai", KEY_B, "gpt-4o-mini")
    llm = CallerLLM(_selector(_with_env(settings), store, box))
    recorder = Recorder(status={"api.openai.com": 401})
    events: list[tuple[str, dict[str, Any]]] = []

    async def scenario() -> None:
        with use_transport(httpx.MockTransport(recorder)), bind_caller("guest-a"):
            with pytest.raises(CallerKeyFailed) as first:
                await _complete(llm)
            assert current_caller_failure() is first.value
            token = bind_live_emit(lambda event, data: events.append((event, data)))
            try:
                for _ in range(2):
                    with pytest.raises(CallerKeyFailed):
                        await _complete(llm)
            finally:
                reset_live_emit(token)
            assert take_key_notice() is None

    asyncio.run(scenario())
    assert recorder.hosts() == ["api.openai.com"]
    assert events == [("llm_key", {"provider": "openai", "model": "gpt-4o-mini", "reason": "auth"})]


def test_failure_never_heard_live_is_owed_to_the_stream(settings: Settings, store: SQLiteStore,
                                                        box: SecretBox) -> None:
    _save(store, box, "guest-a", "openai", KEY_B, "gpt-4o-mini")
    llm = CallerLLM(_selector(settings, store, box))

    async def scenario() -> tuple[Any, Any]:
        with use_transport(httpx.MockTransport(Recorder(status={"api.openai.com": 401}))), bind_caller("guest-a"):
            assert take_key_notice() is None
            with pytest.raises(CallerKeyFailed):
                await _complete(llm)
            with pytest.raises(CallerKeyFailed):
                await _complete(llm)
            return take_key_notice(), take_key_notice()

    owed, again = asyncio.run(scenario())
    assert owed == {"provider": "openai", "model": "gpt-4o-mini", "reason": "auth"}
    assert again is None
    assert KEY_B not in json.dumps(owed)


def test_unreadable_key_reason_is_unreadable(settings: Settings, store: SQLiteStore, box: SecretBox) -> None:
    other_box = SecretBox(settings.model_copy(update={"auth_secret": "a-different-secret-" + "x" * 32}))
    _save(store, other_box, "guest-a", "openai", KEY_B, "gpt-4o-mini")
    llm = CallerLLM(_selector(settings, store, box))

    async def scenario() -> Any:
        with bind_caller("guest-a"):
            with pytest.raises(CallerKeyFailed):
                await _complete(llm)
            return take_key_notice()

    assert asyncio.run(scenario()) == {"provider": "openai", "model": "gpt-4o-mini", "reason": "unreadable"}


def test_caller_llm_reports_its_source(settings: Settings, store: SQLiteStore, box: SecretBox) -> None:
    _save(store, box, "guest-a", "openai", KEY_B, "gpt-4o-mini")
    llm = CallerLLM(_selector(_with_env(settings), store, box))
    with bind_caller("guest-a"):
        assert llm.source == "byok"
    with bind_caller("guest-b"):
        assert llm.source == "env"
    assert llm.source == "env"


def test_unreadable_server_key_is_logged_once_without_key_text(settings: Settings, store: SQLiteStore,
                                                               box: SecretBox, caplog) -> None:
    other_box = SecretBox(settings.model_copy(update={"auth_secret": "a-different-secret-" + "x" * 32}))
    row = _save(store, other_box, SERVER_OWNER, "openai", SERVER_KEY, "gpt-4o-mini")
    selector = _selector(_with_env(settings), store, box)
    with caplog.at_level("WARNING", logger="app.application.llm_selection"):
        for _ in range(3):
            chosen = selector.operator_chain()
            assert (chosen.source, chosen.problem) == ("server", "unreadable")
    warnings = [record for record in caplog.records if record.name == "app.application.llm_selection"]
    assert len(warnings) == 1
    text = warnings[0].getMessage()
    assert row.id in text
    assert SERVER_KEY not in text and SERVER_KEY[-4:] not in text and row.ciphertext.decode("ascii") not in text


def test_gateway_records_the_llm_source_without_the_key(settings: Settings, store: SQLiteStore,
                                                        box: SecretBox) -> None:
    from app.application.llm_gateway import LLMGateway
    from app.application.prompts import PromptLibrary
    from app.infrastructure.observability import TraceRecorder

    _save(store, box, "guest-a", "openai", KEY_B, "gpt-4o-mini")
    tracer = TraceRecorder("req_test", settings)
    gateway = LLMGateway(CallerLLM(_selector(_with_env(settings), store, box)), PromptLibrary(settings), tracer, settings)
    events: list[tuple[str, dict[str, Any]]] = []

    async def scenario() -> None:
        token = bind_live_emit(lambda event, data: events.append((event, data)))
        try:
            with use_transport(httpx.MockTransport(Recorder())), bind_caller("guest-a"):
                await gateway.json("ask", user="hi", purpose="ask.answer")
        finally:
            reset_live_emit(token)

    asyncio.run(scenario())
    span = next(span for span in tracer.spans if span.name == "llm.ask.answer")
    assert span.attrs["llm_source"] == "byok"
    think = [data for event, data in events if event == "think"]
    assert think and think[0]["llm_source"] == "byok"
    assert tracer.generations[0]["llm_source"] == "byok"
    recorded = json.dumps({"spans": [span.to_dict() for span in tracer.spans], "generations": tracer.generations,
                           "events": events}, default=str)
    assert KEY_B not in recorded and KEY_B[-4:] not in recorded
