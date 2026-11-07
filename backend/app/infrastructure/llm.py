"""OpenAI-compatible writer adapter (DeepSeek / xAI / any chat-completions API)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.domain.errors import ProviderError, ProviderNoData
from app.domain.models import FailureKind
from app.infrastructure.http import classify_status, current_transport
from app.security.scrub import scrub


@dataclass(slots=True)
class LLMResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    reasoning: str = ""


class OpenAICompatibleLLM:
    """One adapter, three possible vendors. No vendor SDK, no hidden prompt."""

    name = "openai-compatible"

    def __init__(
        self,
        settings: Settings,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        label: str | None = None,
    ):
        self.settings = settings
        # One resolution point, shared with the container: Settings decides
        # which vendor label wins and therefore which URL/key/model is used.
        # An explicit argument (a user's own key) takes the place of the setting.
        self._base_url = settings.writer_base_url if base_url is None else base_url
        self._api_key = settings.writer_api_key if api_key is None else api_key
        self._model = settings.writer_model if model is None else model
        self._label = label
        self.available = bool(self._base_url and self._api_key)
        self.name = self._model or "offline"

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
        if not self.available:
            raise ProviderError("No writer model is configured", kind=FailureKind.AUTH, source="llm")
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode and self.settings.llm_json_mode:
            payload["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        data = await self._post(payload, purpose)
        message = self._message(data)
        content = self._content(message)
        usage = data.get("usage") or {}
        return LLMResult(
            text=content,
            model=str(data.get("model") or self._model),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=int((time.perf_counter() - started) * 1000),
            reasoning=self._reasoning(message),
        )

    async def _post(self, payload: dict[str, Any], purpose: str) -> dict[str, Any]:
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        timeout = self.settings.llm_timeout_seconds
        # Counts only rate-limit, server-error and network retries. A field retry (a 400 naming a field we then
        # drop or rename) happens at most once per field and never uses up these retries.
        retries = 0
        while True:
            try:
                async with httpx.AsyncClient(timeout=timeout, transport=current_transport()) as client:
                    response = await client.post(url, headers=headers, json=payload)
                if response.status_code >= 400:
                    if response.status_code in {400, 422}:
                        body = response.text
                        # Some OpenAI-compatible vendors reject response_format. Retry once
                        # without it instead of failing the whole turn.
                        if "response_format" in payload:
                            payload.pop("response_format", None)
                            continue
                        # Newer models want max_completion_tokens; some reject temperature.
                        if "max_tokens" in payload and "max_tokens" in body:
                            payload["max_completion_tokens"] = payload.pop("max_tokens")
                            continue
                        if "temperature" in payload and "temperature" in body:
                            payload.pop("temperature")
                            continue
                    detail = scrub(response.text, [self._api_key])[:300]
                    message = f"{self._label} returned HTTP {response.status_code}" if self._label else ""
                    raise classify_status(response.status_code, "llm", message, detail=detail)
                data = response.json()
                return data if isinstance(data, dict) else {"data": data}
            except ProviderError as exc:
                if exc.kind in {FailureKind.RATE_LIMIT, FailureKind.NETWORK, FailureKind.SERVER_ERROR} and retries < 2:
                    import asyncio

                    retries += 1
                    await asyncio.sleep(0.6 * retries)
                    continue
                raise
            except Exception as exc:
                if retries < 2:
                    import asyncio

                    retries += 1
                    await asyncio.sleep(0.6 * retries)
                    continue
                raise ProviderError(
                    f"writer model failed for {purpose}: {exc.__class__.__name__}",
                    kind=FailureKind.NETWORK,
                    source="llm",
                ) from exc

    @staticmethod
    def _message(data: dict[str, Any]) -> dict[str, Any]:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderNoData("writer model returned no choices", source="llm")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise ProviderNoData("writer model returned no message", source="llm")
        return message

    @staticmethod
    def _content(message: dict[str, Any]) -> str:
        """The answer only. A reasoning field is never the answer."""
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            raise ProviderNoData("writer model returned empty content", source="llm")
        return content.strip()

    @staticmethod
    def _reasoning(message: dict[str, Any]) -> str:
        raw = message.get("reasoning_content")
        if raw in (None, ""):
            raw = message.get("reasoning")
        if isinstance(raw, list):
            raw = "".join(part.get("text", "") for part in raw if isinstance(part, dict))
        if not isinstance(raw, str):
            return ""
        return raw.strip()[:4000]


class NullLLM:
    name = "offline"
    available = False

    async def complete(self, **_: Any) -> LLMResult:
        raise ProviderError("No writer model is configured", kind=FailureKind.AUTH, source="llm")
