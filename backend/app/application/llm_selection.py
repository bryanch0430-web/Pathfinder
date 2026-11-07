"""Which writer serves the current request: the caller's own key, else the operator chain.

Resolution order (design decision 9): the caller's chosen key, the server key from the admin card,
the ``.env`` writer, the local model when its probe says it is reachable, then ``NullLLM``. One
source is chosen before the call and nothing else is tried when it fails (decisions 4 and 10).

The caller is bound per request in a ContextVar (``bind_caller``). ``asyncio.create_task`` copies
the context, so child tasks share the request's memoised selection and its remembered failure.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Literal

import httpx

from app.config import Settings
from app.domain.credentials import SERVER_OWNER, StoredCredential
from app.domain.errors import CallerKeyFailed, ProviderError, ProviderNoData, operator_text
from app.domain.models import FailureKind
from app.domain.ports import LLM
from app.infrastructure.http import current_transport
from app.infrastructure.llm import NullLLM, OpenAICompatibleLLM
from app.infrastructure.llm_providers import PROVIDERS, build_llm
from app.infrastructure.observability import emit_live
from app.infrastructure.secret_box import SecretBox, UnreadableSecret
from app.infrastructure.store import SQLiteStore
from app.security.scrub import scrub

Source = Literal["byok", "server", "env", "local", "none"]

UNREADABLE_KEY_MESSAGE = "Your saved key can no longer be read. Remove it and add it again in Settings."
SERVER_UNREADABLE_MESSAGE = "The server's saved key can no longer be read. Add it again in the admin Settings."
ADAPTER_CACHE_SIZE = 64

#: What a traveller reads after "Your <provider> key failed: ". Written by us, never the provider's text.
_REASONS: dict[FailureKind, str] = {
    FailureKind.AUTH: "the provider refused it. Check the key in Settings.",
    FailureKind.RATE_LIMIT: "it is rate-limited or out of credit. Check the key in Settings.",
    FailureKind.TIMEOUT: "the provider did not answer in time. Check the key in Settings.",
    FailureKind.NETWORK: "the provider could not be reached. Check the key in Settings.",
    FailureKind.INVALID_REQUEST: "the provider refused the request; the model may not be available. "
    "Check the key in Settings.",
    FailureKind.NO_DATA: "the provider did not find the model. Check the key in Settings.",
}
_DEFAULT_REASON = "the provider returned an error. Check the key in Settings."


@dataclass
class Selection:
    llm: LLM
    source: Source
    provider: str
    model: str
    credential_id: str = ""
    problem: str = ""  # set when the chosen key cannot be used: "unreadable" or "unknown_provider"


@dataclass
class SearchKey:
    api_key: str = field(repr=False)
    model: str
    #: False when the caller's xAI row cannot be decrypted: search fails for them, never using the operator key.
    usable: bool = True


@dataclass
class _Adapter:
    llm: LLM
    secret: str = field(repr=False)


class _UnusableKeyLLM:
    """Stands in for a saved key that cannot be used. It fails instead of letting another source answer."""

    def __init__(self, model: str, *, available: bool, error: ProviderError) -> None:
        self.name = model or "offline"
        self.available = available
        self._error = error

    async def complete(self, **_: Any) -> Any:
        raise self._error


class LocalProbe:
    """Whether the operator's local model answers, cached for ``ttl_seconds``. Never raises.

    Synchronous because ``gateway.available`` is a sync property: at most one probe-timeout stall
    happens per TTL.
    """

    def __init__(
        self,
        settings: Settings,
        ttl_seconds: float = 60.0,
        *,
        check: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._ttl = ttl_seconds
        self._check = check or self._get_models
        self._clock = clock
        self._lock = threading.Lock()
        self._checked_at: float | None = None
        self._reachable = False

    def reachable(self) -> bool:
        if not self._settings.local_llm_configured:
            return False
        # Held across the check, so concurrent callers wait for one probe instead of each sending one.
        with self._lock:
            now = self._clock()
            if self._checked_at is not None and now - self._checked_at < self._ttl:
                return self._reachable
            try:
                self._reachable = bool(self._check())
            except Exception:
                self._reachable = False
            self._checked_at = now
            return self._reachable

    def _get_models(self) -> bool:
        settings = self._settings
        transport = current_transport()
        headers = {"Authorization": f"Bearer {settings.local_llm_api_key}"} if settings.local_llm_api_key else {}
        with httpx.Client(
            timeout=settings.local_llm_probe_seconds,
            transport=transport if isinstance(transport, httpx.BaseTransport) else None,
        ) as client:
            response = client.get(f"{settings.local_llm_base_url.rstrip('/')}/models", headers=headers)
        return 200 <= response.status_code < 300


class LLMSelector:
    def __init__(
        self,
        settings: Settings,
        store: SQLiteStore,
        box: SecretBox,
        *,
        probe: LocalProbe | None = None,
    ) -> None:
        self._settings = settings
        self._store = store
        self._box = box
        self._probe = probe or LocalProbe(settings)
        self._lock = threading.Lock()
        self._adapters: OrderedDict[tuple[str, str], _Adapter] = OrderedDict()
        # The non-server part of the operator chain is fixed for the process: settings do not change.
        self._env: Selection | None = None
        if settings.writer_available:
            env_llm = OpenAICompatibleLLM(settings)
            self._env = Selection(env_llm, "env", settings.llm_provider, env_llm.name)
        self._local: Selection | None = None
        if settings.local_llm_configured:
            local_llm = OpenAICompatibleLLM(
                settings,
                base_url=settings.local_llm_base_url,
                api_key=settings.local_llm_api_key or "local",
                model=settings.local_llm_model,
                label="Local model",
            )
            self._local = Selection(local_llm, "local", "local", settings.local_llm_model)
        self._none = Selection(NullLLM(), "none", "", NullLLM.name)

    def operator_chain(self) -> Selection:
        row = self._store.active_credential(SERVER_OWNER)
        if row is not None:
            return self._from_row(row, "server")
        if self._env is not None:
            return self._env
        if self._local is not None and self._probe.reachable():
            return self._local
        return self._none

    def for_caller(self, owner: str | None) -> Selection:
        if _is_caller(owner):
            row = self._store.active_credential(owner)  # type: ignore[arg-type]
            if row is not None:
                return self._from_row(row, "byok")
        return self.operator_chain()

    def search_key(self, owner: str | None) -> SearchKey | None:
        if not _is_caller(owner):
            return None
        row = self._store.search_credential(owner)  # type: ignore[arg-type]
        if row is None:
            return None
        try:
            api_key = self._box.decrypt(row.ciphertext)
        except UnreadableSecret:
            return SearchKey(api_key="", model="", usable=False)
        return SearchKey(api_key=api_key, model=row.model or PROVIDERS["xai"].default_model)

    def local_status(self) -> dict[str, Any]:
        """The operator's local model, for the Settings card: configured, answering, and which model."""
        return {
            "configured": self._settings.local_llm_configured,
            "reachable": self._probe.reachable(),
            "model": self._settings.local_llm_model,
        }

    def forget(self, credential_id: str) -> None:
        """Drop the cached adapter (and the key it holds) for a row that was changed or removed."""
        with self._lock:
            for stale in [key for key in self._adapters if key[0] == credential_id]:
                del self._adapters[stale]

    def secret_for(self, credential_id: str) -> str:
        """The key behind a cached adapter, for scrubbing error text; "" once it has left the cache."""
        with self._lock:
            for (cached_id, _), adapter in self._adapters.items():
                if cached_id == credential_id:
                    return adapter.secret
        return ""

    def _from_row(self, row: StoredCredential, source: Source) -> Selection:
        spec = PROVIDERS.get(row.provider)
        model = row.model or (spec.default_model if spec else "")
        if spec is None:
            return self._unusable(row, source, model, "unknown_provider")
        cache_key = (row.id, row.updated_at)
        with self._lock:
            adapter = self._adapters.get(cache_key)
            if adapter is not None:
                self._adapters.move_to_end(cache_key)
        if adapter is None:
            try:
                api_key = self._box.decrypt(row.ciphertext)
            except UnreadableSecret:
                return self._unusable(row, source, model, "unreadable")
            adapter = _Adapter(build_llm(spec, api_key, model, self._settings), api_key)
            with self._lock:
                # An older version of the same row (another model or key) is never served again.
                for stale in [key for key in self._adapters if key[0] == row.id]:
                    del self._adapters[stale]
                self._adapters[cache_key] = adapter
                while len(self._adapters) > ADAPTER_CACHE_SIZE:
                    self._adapters.popitem(last=False)
        return Selection(adapter.llm, source, row.provider, model, row.id)

    @staticmethod
    def _unusable(row: StoredCredential, source: Source, model: str, problem: str) -> Selection:
        if source == "byok":
            # Available, so the call is made and fails visibly instead of quietly running offline.
            llm = _UnusableKeyLLM(model, available=True, error=CallerKeyFailed(UNREADABLE_KEY_MESSAGE))
        else:
            # The operator's key: no fall-through to the .env writer (decision 10); turns run offline.
            error = ProviderError(SERVER_UNREADABLE_MESSAGE, kind=FailureKind.AUTH, source="llm", retryable=False)
            llm = _UnusableKeyLLM(model, available=False, error=error)
        return Selection(llm, source, row.provider, model, row.id, problem=problem)


def _is_caller(owner: str | None) -> bool:
    # "server" is the operator's own row: a client that sends it as its token is not that owner.
    return bool(owner) and owner != SERVER_OWNER


@dataclass
class _CallerState:
    owner: str | None
    selection: Selection | None = None
    failed: CallerKeyFailed | None = None
    search: SearchKey | None = None
    search_loaded: bool = False


_caller: ContextVar[_CallerState | None] = ContextVar("pathfinder_llm_caller", default=None)


@contextmanager
def bind_caller(owner: str | None) -> Iterator[None]:
    """Bind the request's caller. Its key is read the first time the request uses the writer or search."""
    token = _caller.set(_CallerState(owner))
    try:
        yield
    finally:
        try:
            _caller.reset(token)
        except ValueError:
            # Unwound in another context than the one that bound it: nothing there to restore.
            pass


def current_owner() -> str | None:
    state = _caller.get()
    return state.owner if state is not None else None


def current_search_key(selector: LLMSelector) -> SearchKey | None:
    state = _caller.get()
    if state is None:
        return None
    if not state.search_loaded:
        state.search = selector.search_key(state.owner)
        state.search_loaded = True
    return state.search


class CallerLLM:
    """The ``LLM`` port for the current request: delegates to the bound caller's selection.

    With no binding (CLI, evals, tests that build the container directly) it uses the operator chain.
    """

    def __init__(self, selector: LLMSelector) -> None:
        self._selector = selector

    def _selection(self) -> Selection:
        state = _caller.get()
        if state is None:
            return self._selector.operator_chain()
        if state.selection is None:
            state.selection = self._selector.for_caller(state.owner)
        return state.selection

    @property
    def available(self) -> bool:
        return bool(getattr(self._selection().llm, "available", False))

    @property
    def name(self) -> str:
        return str(getattr(self._selection().llm, "name", "offline"))

    async def complete(
        self,
        *,
        system: str,
        user: str,
        purpose: str,
        json_mode: bool = True,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> Any:
        state = _caller.get()
        if state is not None and state.failed is not None:
            raise state.failed
        selection = self._selection()
        try:
            return await selection.llm.complete(
                system=system,
                user=user,
                purpose=purpose,
                json_mode=json_mode,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except ProviderError as exc:
            if selection.source != "byok" or _empty_reply(exc):
                raise
            if state is not None and state.failed is not None:
                # A concurrent call of this request failed first and already told the traveller.
                raise state.failed
            failure = self._caller_failure(selection, exc)
            if state is not None:
                state.failed = failure
            emit_live("llm_key", {"provider": selection.provider, "model": selection.model, "reason": failure.kind.value})
            raise failure from exc

    def _caller_failure(self, selection: Selection, exc: ProviderError) -> CallerKeyFailed:
        if isinstance(exc, CallerKeyFailed):
            return exc
        spec = PROVIDERS.get(selection.provider)
        label = spec.label if spec else selection.provider
        reason = _REASONS.get(exc.kind, _DEFAULT_REASON)
        detail = scrub(operator_text(exc), [self._selector.secret_for(selection.credential_id)])[:300]
        return CallerKeyFailed(f"Your {label} key failed: {reason}", kind=exc.kind, source="llm", detail=detail)


def _empty_reply(exc: ProviderError) -> bool:
    """A 2xx without text: the key worked, so this is not the key failing (a 404 is ProviderNoData too)."""
    return isinstance(exc, ProviderNoData) and str(exc).startswith("writer model returned")
