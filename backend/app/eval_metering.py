"""Offline cost accounting for evaluation runs.

Two small pieces that let a sandbox run observe what a live run would bill:

* ``MeteredLLM`` is a writer stand-in. It answers every prompt with a fixed,
  schema-valid reply and reports usage the way a chat-completions API does
  (prompt and completion tokens per call). Tokens are estimated at four
  characters per token; with a live writer the gateway counters carry the
  vendor's own usage figures instead.
* ``RunLedger`` is the per-run account: it counts every provider invocation at
  the provider itself and keeps a private SQLite tool log (and counts any other
  tool log it is asked to track), so "tool calls made" can be compared with
  "tool calls logged" (Proposal TC-12), and it adds up the writer tokens a run
  used (Proposal TC-10).

Offline exchange rates are the sandbox's own ``evaluation.SandboxFx``.
"""

from __future__ import annotations

import functools
import inspect
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from app.domain.models import EvalMetric
from app.infrastructure.llm import LLMResult
from app.infrastructure.store import SQLiteStore

CHARS_PER_TOKEN = 4

# Registry attributes that hold providers, and the provider methods that are a
# tool call (one network request each in the live adapters).
PROVIDER_GROUPS = (
    "geocoder",
    "fx",
    "timezone",
    "elevation",
    "places",
    "weather",
    "hotels",
    "flights",
    "notes",
    "routes",
    "warnings",
    "transit",
    "transfers",
    "web",
    "web_deep",
)
PROVIDER_METHODS = (
    "geocode", "search", "forecast", "leg", "matrix", "segments", "resolve", "gain", "answer", "refresh", "alerts", "status",
)
# Tool-log statuses of a call that reached the provider.
INVOKED_STATUSES = frozenset({"ok", "error"})

DEFAULT_REPLIES: dict[str, str] = {
    "ask.answer": json.dumps({"answer": "The itinerary covers that; here is the relevant part."}),
    "clarify": json.dumps({"question": "Which part of the trip should I look at?", "slots": []}),
}


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return (len(text) + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


class MeteredLLM:
    """A deterministic writer that reports token usage for every call.

    ``replies`` maps a gateway purpose (``"planner"``, ``"plan.repair"``) or its
    first segment (``"modify"``) to the raw text to return. The router reply is
    taken from ``route`` so a harness can script the decision per utterance.
    Anything else gets ``{}``, which every call site treats like its offline
    fallback.
    """

    name = "sandbox-metered"
    available = True

    def __init__(self, replies: dict[str, str] | None = None):
        self.replies = {**DEFAULT_REPLIES, **(replies or {})}
        self.route: dict[str, Any] | None = None
        self.purposes: list[str] = []
        self.prompt_tokens = 0
        self.completion_tokens = 0

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    async def complete(
        self,
        *,
        system: str,
        user: str,
        purpose: str,
        json_mode: bool = True,
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ) -> LLMResult:
        text = self.reply(purpose)
        prompt_tokens = estimate_tokens(system) + estimate_tokens(user)
        completion_tokens = estimate_tokens(text)
        self.purposes.append(purpose)
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        return LLMResult(
            text=text,
            model=self.name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )

    def reply(self, purpose: str) -> str:
        if purpose in self.replies:
            return self.replies[purpose]
        base = purpose.split(".")[0]
        if base == "router":
            return json.dumps(self.route or {"intent": "unclear", "confidence": 0.0})
        return self.replies.get(base, "{}")


class RunLedger:
    """Per-run account of provider invocations, logged tool spans and tokens."""

    def __init__(self) -> None:
        self._tmp: tempfile.TemporaryDirectory | None = None
        self._store: SQLiteStore | None = None
        self._logs: list[Any] = []
        self.made: Counter[str] = Counter()
        self.tokens = 0

    def track(self, log: Any) -> None:
        """Also count the entries of another tool log (e.g. a case's eval_checks.ToolLog)."""
        self._logs.append(log)

    @property
    def directory(self) -> Path:
        if self._tmp is None:
            self._tmp = tempfile.TemporaryDirectory(prefix="pathfinder-ledger-", ignore_cleanup_errors=True)
        return Path(self._tmp.name)

    @property
    def store(self) -> SQLiteStore:
        """A private store whose tool log holds only this run's spans."""
        if self._store is None:
            self._store = SQLiteStore(self.directory / "ledger.db")
        return self._store

    def instrument(self, registry: Any) -> Any:
        """Count each provider call where it happens, below the toolbox."""
        seen: set[int] = set()
        for group in PROVIDER_GROUPS:
            value = getattr(registry, group, None)
            for source in value if isinstance(value, list) else [value]:
                if source is None or id(source) in seen:
                    continue
                seen.add(id(source))
                for method in PROVIDER_METHODS:
                    bound = getattr(source, method, None)
                    if bound is not None and inspect.iscoroutinefunction(bound):
                        setattr(source, method, self._counted(source, method, bound))
        return registry

    def _counted(self, owner: Any, method: str, bound: Any) -> Any:
        key = f"{type(owner).__name__}.{method}"
        made = self.made

        @functools.wraps(bound)
        async def counted(*args: Any, **kwargs: Any) -> Any:
            made[key] += 1
            return await bound(*args, **kwargs)

        # The toolbox names the source class in the log from ``fn.__self__``.
        counted.__self__ = owner  # type: ignore[attr-defined]
        return counted

    @property
    def made_total(self) -> int:
        return sum(self.made.values())

    def logged(self, request_id: str | None = None) -> int:
        """Tool spans that record an invocation (a blocked or malformed call never ran)."""
        return sum(self.logged_tools(request_id).values())

    def logged_tools(self, request_id: str | None = None) -> Counter[str]:
        """Logged invocations by tool: one request's, or the whole run's (tracked logs included)."""
        rows = list(self._store.tool_calls(request_id)) if self._store is not None else []
        if request_id is None:
            rows += [entry for log in self._logs for entry in log.entries]
        return Counter(row["tool"] for row in rows if row["status"] in INVOKED_STATUSES)

    def coverage_metric(self) -> EvalMetric:
        made = self.made_total
        logged = self.logged()
        return EvalMetric(
            key="tool_log_coverage",
            label=f"Tool calls logged ({logged}/{made})",
            value=round(100.0 * logged / made, 1) if made else 100.0,
            unit="%",
        )

    def close(self) -> None:
        self._store = None
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None
