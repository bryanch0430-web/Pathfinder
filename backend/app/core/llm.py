"""The model gateway: one OpenAI-compatible client for every role in the app.

Pathfinder v5 deliberately has **one** way to talk to a model, and every call is
typed by ``purpose`` ("understand", "plan", "ask", "verify", "translate", ...).
That makes the system observable (a trace row per call), swappable (Poe today,
xAI or any compatible endpoint tomorrow) and testable (a fake client in tests).

Structured calls use a JSON contract:

1. the system prompt states the contract and gives the JSON skeleton,
2. the reply is parsed defensively (a model may wrap JSON in prose or fences),
3. the parsed object is validated by a Pydantic schema,
4. on a parse or validation failure the model gets **one** repair turn that
   includes the exact error, then the call fails loudly.

There is no regex-based interpretation of the traveller's text anywhere here:
understanding, planning and edits are the model's job, with the schemas making
its answer machine-checkable.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.core.trace import TurnTrace

TModel = TypeVar("TModel", bound=BaseModel)

# Built-in reply caps per part. A Models override wins; then an explicit call cap;
# then this map; then the writer default.
PURPOSE_MAX_TOKENS = {
    "plan": 8000,
    "edit": 3000,
    "verify": 1500,
    "ask": 1400,
    "understand": 3000,
    "intake": 6000,
    "memory": 700,
    "search_query": 200,
    "translate": 4000,
}


class LLMError(RuntimeError):
    """The model could not answer: a network failure, a refused request, or unusable output."""

    def __init__(self, message: str, *, retryable: bool = True, status: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status = status


@dataclass
class LLMReply:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    reasoning: str = ""


class LLMClient:
    def __init__(self, settings: Settings, *, trace: TurnTrace | None = None) -> None:
        self.settings = settings
        self.trace = trace
        base, key, model = settings.writer
        self.base_url = base
        self.api_key = key
        self.model = model
        self._client: httpx.AsyncClient | None = None

    # -- transport ----------------------------------------------------

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.llm_timeout_seconds, connect=15.0),
                limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def _uses_verifier_provider(self, purpose: str) -> bool:
        if not self.settings.verifier_api_key:
            return False
        configured = (self.settings.purpose_models or {}).get(purpose, "").strip()
        if purpose == "verify":
            # Default and other DeepSeek names use the verifier endpoint; an
            # explicit model from the writer provider keeps the writer endpoint.
            return not configured or configured.lower().startswith("deepseek")
        # A light part can borrow the fast verifier endpoint by naming a DeepSeek
        # model. An empty entry keeps the writer, so nothing changes by default.
        return bool(configured) and configured.lower().startswith("deepseek")

    def provider_for(self, purpose: str) -> tuple[str, str]:
        """Endpoint and key for a part; the verifier may use its own DeepSeek key."""
        if self._uses_verifier_provider(purpose):
            return self.settings.verifier_base_url.rstrip("/"), self.settings.verifier_api_key
        return self.base_url, self.api_key

    def available_for(self, purpose: str) -> bool:
        base, key = self.provider_for(purpose)
        return bool(base and key and self.model_for(purpose))

    def model_for(self, purpose: str, model: str | None = None) -> str:
        """The model this part uses: explicit, then configured, then verifier/writer."""
        configured = (self.settings.purpose_models or {}).get(purpose, "").strip()
        if model:
            return model.strip()
        if configured:
            return configured
        if purpose == "verify" and self._uses_verifier_provider("verify"):
            return self.settings.verifier_model.strip()
        return self.model

    def temperature_for(self, purpose: str, temperature: float | None = None) -> float:
        if temperature is not None:
            return temperature
        configured = (self.settings.purpose_temperatures or {}).get(purpose)
        if configured is None:
            if self._uses_verifier_provider(purpose):
                return max(0.0, min(2.0, float(self.settings.verifier_temperature)))
            return self.settings.llm_temperature
        try:
            return max(0.0, min(2.0, float(configured)))
        except (TypeError, ValueError):
            return self.settings.llm_temperature

    def reasoning_for(self, purpose: str) -> str:
        configured = (self.settings.purpose_reasoning or {}).get(purpose)
        return (configured or self.settings.llm_reasoning or "none").strip().lower()

    def max_tokens_for(self, purpose: str, max_tokens: int | None = None) -> int:
        """A part's explicit cap, then the call's own cap, then the part default."""
        configured = (self.settings.purpose_max_tokens or {}).get(purpose)
        if configured:
            try:
                return max(16, min(32000, int(configured)))
            except (TypeError, ValueError):
                pass
        if max_tokens:
            return max_tokens
        # The part's own cap beats the verifier default. Otherwise a light part
        # moved to the fast endpoint would be cut at the verifier's 1,500 tokens.
        if purpose in PURPOSE_MAX_TOKENS:
            return PURPOSE_MAX_TOKENS[purpose]
        if self._uses_verifier_provider(purpose):
            return max(16, min(32000, int(self.settings.verifier_max_tokens)))
        return self.settings.llm_max_tokens

    def _payload(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
        json_mode: bool,
        stream: bool,
        reasoning: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "temperature": self.settings.llm_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or self.settings.llm_max_tokens,
            "stream": stream,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        effort = (reasoning or "none").strip().lower()
        # Only OpenAI reasoning models take reasoning_effort; Claude and others ignore it.
        model_name = str(payload["model"]).lower()
        if effort in {"low", "medium", "high"} and model_name.startswith(("gpt-5", "o1", "o3", "o4")):
            payload["reasoning_effort"] = effort
        return payload

    async def _post(self, payload: dict[str, Any], *, purpose: str) -> dict[str, Any]:
        """POST once, retrying idempotent failures (429/5xx/timeouts) with jittered backoff."""
        base, key = self.provider_for(purpose)
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        timeout = self.settings.verifier_timeout_seconds if purpose == "verify" else None
        last: Exception | None = None
        for attempt in range(2):
            started = time.perf_counter()
            try:
                response = await self.client.post(
                    f"{base}/chat/completions", json=payload, headers=headers, timeout=timeout
                )
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise LLMError(
                        f"model endpoint returned {response.status_code}",
                        status=response.status_code,
                    )
                if response.status_code >= 400:
                    detail = response.text[:400]
                    raise LLMError(
                        f"model request failed ({response.status_code}): {detail}",
                        retryable=response.status_code in {401, 403, 404},
                        status=response.status_code,
                    )
                data = response.json()
                data["_latency_ms"] = (time.perf_counter() - started) * 1000
                return data
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last = LLMError(f"model endpoint unreachable: {exc}")
            except LLMError as exc:
                last = exc
                if exc.retryable and exc.status in {401, 403, 404}:
                    break
            if attempt < 1:
                await asyncio.sleep(0.6 * (2**attempt) + random.random() * 0.3)
        self.trace and self.trace.say(f"The {purpose} model call did not complete: {last}")
        raise last if last else LLMError("model request failed")

    # -- plain text / streaming --------------------------------------

    async def text(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMReply:
        if not self.available_for(purpose):
            raise LLMError("no model is configured for this part", retryable=False)
        chosen = self.model_for(purpose, model)
        payload = self._payload(
            system=system,
            messages=[{"role": "user", "content": user}],
            model=chosen,
            temperature=self.temperature_for(purpose, temperature),
            max_tokens=self.max_tokens_for(purpose, max_tokens),
            json_mode=False,
            stream=False,
            reasoning=self.reasoning_for(purpose),
        )
        data = await self._post(payload, purpose=purpose)
        choice = (data.get("choices") or [{}])[0]
        content = (choice.get("message") or {}).get("content") or ""
        usage = data.get("usage") or {}
        reply = LLMReply(
            text=content,
            model=data.get("model") or payload["model"],
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=float(data.get("_latency_ms") or 0),
            reasoning=_reasoning_of(choice),
        )
        self._record(purpose, reply, user)
        return reply

    async def stream_text(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Yield text deltas. Falls back to a single-shot call if the endpoint refuses streaming."""
        if not self.available_for(purpose):
            raise LLMError("no model is configured for this part", retryable=False)
        chosen = self.model_for(purpose, model)
        payload = self._payload(
            system=system,
            messages=[{"role": "user", "content": user}],
            model=chosen,
            temperature=self.temperature_for(purpose, temperature),
            max_tokens=self.max_tokens_for(purpose, max_tokens),
            json_mode=False,
            stream=True,
            reasoning=self.reasoning_for(purpose),
        )
        base, key = self.provider_for(purpose)
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        started = time.perf_counter()
        chunks: list[str] = []
        prompt_tokens = completion_tokens = 0
        streamed = False
        try:
            async with self.client.stream(
                "POST", f"{base}/chat/completions", json=payload, headers=headers
            ) as response:
                if response.status_code >= 400:
                    raise LLMError(f"stream failed ({response.status_code})", status=response.status_code)
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    usage = event.get("usage") or {}
                    prompt_tokens = int(usage.get("prompt_tokens") or prompt_tokens)
                    completion_tokens = int(usage.get("completion_tokens") or completion_tokens)
                    delta = (((event.get("choices") or [{}])[0].get("delta")) or {}).get("content") or ""
                    if delta:
                        streamed = True
                        chunks.append(delta)
                        yield delta
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if not chunks:
                raise LLMError(f"model endpoint unreachable: {exc}") from exc
        except LLMError:
            if not chunks:
                raise
        if not streamed:
            reply = await self.text(purpose=purpose, system=system, user=user, model=chosen, temperature=temperature)
            chunks.append(reply.text)
            prompt_tokens, completion_tokens = reply.prompt_tokens, reply.completion_tokens
            yield reply.text
        self._record(
            purpose,
            LLMReply(
                text="".join(chunks),
                model=chosen,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=(time.perf_counter() - started) * 1000,
            ),
            user,
        )

    # -- structured ---------------------------------------------------

    async def json(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        schema: type[TModel],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        attempt_result: BaseModel | None = None,
        images: list[str] | None = None,
    ) -> TModel:
        """Ask for one JSON object, validate it, and give the model one repair turn."""
        if not self.available:
            raise LLMError("no writer model is configured", retryable=False)
        chosen = self.model_for(purpose, model)
        messages: list[dict[str, Any]] = []
        if images:
            content: list[dict[str, Any]] = [{"type": "text", "text": user}]
            for url in images[:4]:
                content.append({"type": "image_url", "image_url": {"url": url}})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": user})

        last_error = ""
        for turn in range(2):
            payload = self._payload(
                system=system,
                messages=messages,
                model=chosen,
                temperature=self.temperature_for(purpose, temperature) if turn == 0 else 0.0,
                max_tokens=self.max_tokens_for(purpose, max_tokens),
                json_mode=True,
                stream=False,
                reasoning=self.reasoning_for(purpose),
            )
            try:
                data = await self._post(payload, purpose=purpose)
            except LLMError as exc:
                # Some compatible endpoints reject `response_format`; retry once without it.
                if exc.status in {400, 422} and payload.get("response_format"):
                    payload.pop("response_format", None)
                    data = await self._post(payload, purpose=purpose)
                else:
                    raise
            choice = (data.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content") or ""
            usage = data.get("usage") or {}
            reply = LLMReply(
                text=content,
                model=data.get("model") or payload["model"],
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                latency_ms=float(data.get("_latency_ms") or 0),
                reasoning=_reasoning_of(choice),
            )
            self._record(purpose, reply, user)
            parsed = extract_json(content)
            if parsed is not None:
                try:
                    return schema.model_validate(parsed)
                except ValidationError as exc:
                    last_error = _short_validation(exc)
            else:
                last_error = "the reply was not a JSON object"
            if turn == 0:
                messages = [
                    {"role": "user", "content": user},
                    {"role": "assistant", "content": content[:6000]},
                    {
                        "role": "user",
                        "content": (
                            "That reply did not satisfy the contract (" + last_error + "). "
                            "Reply again with ONE JSON object only, same schema, fixing that problem. "
                            "No prose, no markdown fences."
                        ),
                    },
                ]
                if self.trace:
                    self.trace.say(f"The {purpose} answer needed one repair: {last_error}")
        raise LLMError(
            f"the model's {purpose} answer was unusable: {last_error} | raw: {(content or '')[:500]}",
            retryable=False,
        )

    async def json_stream(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        schema: type[TModel],
        on_text: Callable[[str], Awaitable[None]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        deadline: float | None = None,
    ) -> TModel:
        """Stream one JSON answer and validate it once it is whole.

        `on_text` sees the text written so far, so a caller can show a partial
        result. There is no repair turn here: on any failure the caller falls
        back to `json()`, which keeps the old behaviour.
        """
        if not self.available_for(purpose):
            raise LLMError("no model is configured for this part", retryable=False)
        chosen = self.model_for(purpose, model)
        payload = self._payload(
            system=system,
            messages=[{"role": "user", "content": user}],
            model=chosen,
            temperature=self.temperature_for(purpose, temperature),
            max_tokens=self.max_tokens_for(purpose, max_tokens),
            json_mode=True,
            stream=True,
            reasoning=self.reasoning_for(purpose),
        )
        base, key = self.provider_for(purpose)
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        started = time.perf_counter()
        # A provider may keep the stream open with keepalive lines after it has
        # said everything. Without this wall clock the turn waited forever.
        # It is not the per-call idle timeout: one stream may legitimately write
        # a seven-day plan for a couple of minutes.
        deadline = started + max(120.0, min(240.0, float(self.settings.turn_deadline_seconds) * 0.6)) if deadline is None else deadline
        limit = max(2000, int(self.max_tokens_for(purpose, max_tokens)) * 8)
        chunks: list[str] = []
        total_chars = 0
        prompt_tokens = completion_tokens = 0
        try:
            async with self.client.stream(
                "POST", f"{base}/chat/completions", json=payload, headers=headers
            ) as response:
                if response.status_code in {400, 422}:
                    # The endpoint refuses response_format or streaming; the
                    # caller's json() path retries without them.
                    raise LLMError(f"stream refused ({response.status_code})", status=response.status_code)
                if response.status_code >= 400:
                    raise LLMError(f"stream failed ({response.status_code})", status=response.status_code)
                async for line in response.aiter_lines():
                    if time.perf_counter() > deadline:
                        raise LLMError("the stream ran past its time limit", retryable=True)
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    usage = event.get("usage") or {}
                    prompt_tokens = int(usage.get("prompt_tokens") or prompt_tokens)
                    completion_tokens = int(usage.get("completion_tokens") or completion_tokens)
                    delta = (((event.get("choices") or [{}])[0].get("delta")) or {}).get("content") or ""
                    if not delta:
                        continue
                    chunks.append(delta)
                    total_chars += len(delta)
                    if total_chars > limit:
                        raise LLMError("the streamed answer was longer than its cap", retryable=False)
                    if on_text is not None:
                        await on_text("".join(chunks))
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if not chunks:
                raise LLMError(f"model endpoint unreachable: {exc}") from exc
        text = "".join(chunks)
        self._record(
            purpose,
            LLMReply(
                text=text,
                model=chosen,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=(time.perf_counter() - started) * 1000,
            ),
            user,
        )
        parsed = extract_json(text)
        if parsed is None:
            raise LLMError("the streamed answer was not a JSON object", retryable=False)
        try:
            return schema.model_validate(parsed)
        except ValidationError as exc:
            raise LLMError(f"the streamed answer was unusable: {_short_validation(exc)}", retryable=False) from exc

    # -- judge --------------------------------------------------------
    async def judge(
        self,
        *,
        purpose: str,
        system: str,
        user: str,
        schema: type[TModel],
        model: str | None = None,
    ) -> TModel:
        """A second, independent model (DeepSeek by default) for grading. Optional."""
        base = self.settings.judge_base_url or "https://api.deepseek.com"
        key = self.settings.judge_api_key
        chosen = model or self.settings.judge_model
        if not (base and key and chosen):
            raise LLMError("no judge model is configured", retryable=False)
        payload = {
            "model": chosen,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.0,
            "max_tokens": 1200,
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        started = time.perf_counter()
        try:
            response = await self.client.post(
                f"{base.rstrip('/')}/chat/completions",
                json=payload,
                headers=headers,
                timeout=self.settings.judge_timeout_seconds,
            )
            response.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            raise LLMError(f"judge unavailable: {exc}") from exc
        data = response.json()
        content = (((data.get("choices") or [{}])[0].get("message")) or {}).get("content") or ""
        usage = data.get("usage") or {}
        if self.trace:
            self.trace.generation(
                purpose=purpose,
                model=chosen,
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
                latency_ms=(time.perf_counter() - started) * 1000,
                output_preview=content,
                user_preview=user,
            )
        parsed = extract_json(content)
        if parsed is None:
            raise LLMError("judge answer was not JSON", retryable=False)
        return schema.model_validate(parsed)

    # -- internals ----------------------------------------------------

    def _record(self, purpose: str, reply: LLMReply, user: str) -> None:
        if self.trace:
            self.trace.generation(
                purpose=purpose,
                model=reply.model,
                prompt_tokens=reply.prompt_tokens,
                completion_tokens=reply.completion_tokens,
                latency_ms=reply.latency_ms,
                reasoning=reply.reasoning,
                output_preview=reply.text,
                user_preview=user,
            )


def _reasoning_of(choice: dict[str, Any]) -> str:
    message = choice.get("message") or {}
    for key in ("reasoning_content", "reasoning"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value[:4000]
    return ""


def _short_validation(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors()[:6]:
        location = ".".join(str(item) for item in error.get("loc", ()))
        parts.append(f"{location}: {error.get('msg', '')}")
    return "; ".join(parts) or str(exc)


def extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model reply.

    Not a semantic parser: it only removes what models add around JSON (fences,
    a leading sentence).  Anything that is not a JSON object returns ``None`` and
    the caller asks the model to repair.
    """
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    start = cleaned.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = cleaned[start : index + 1]
                try:
                    value = json.loads(candidate)
                except json.JSONDecodeError:
                    return None
                return value if isinstance(value, dict) else None
    return None
