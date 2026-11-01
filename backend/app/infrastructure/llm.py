"""OpenAI-compatible writer adapter (DeepSeek / xAI / any chat-completions API)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.domain.errors import ProviderError, ProviderNoData
from app.domain.models import FailureKind
from app.infrastructure.http import classify_status


@dataclass(slots=True)
class LLMResult:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0


class OpenAICompatibleLLM:
    """One adapter, three possible vendors. No vendor SDK, no hidden prompt."""

    name = "openai-compatible"

    def __init__(self, settings: Settings):
        self.settings = settings
        # One resolution point, shared with the container: Settings decides
        # which vendor label wins and therefore which URL/key/model is used.
        self._base_url = settings.writer_base_url
        self._api_key = settings.writer_api_key
        self._model = settings.writer_model
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
        content = self._content(data)
        usage = data.get("usage") or {}
        return LLMResult(
            text=content,
            model=str(data.get("model") or self._model),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def _post(self, payload: dict[str, Any], purpose: str) -> dict[str, Any]:
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        timeout = self.settings.llm_timeout_seconds
        attempts = 0
        while True:
            attempts += 1
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(url, headers=headers, json=payload)
                if response.status_code >= 400:
                    # Some OpenAI-compatible vendors reject response_format. Retry once
                    # without it instead of failing the whole turn.
                    if response.status_code in {400, 422} and "response_format" in payload:
                        payload.pop("response_format", None)
                        continue
                    raise classify_status(response.status_code, "llm", response.text[:300])
                data = response.json()
                return data if isinstance(data, dict) else {"data": data}
            except ProviderError as exc:
                if exc.kind in {FailureKind.RATE_LIMIT, FailureKind.NETWORK, FailureKind.SERVER_ERROR} and attempts <= 2:
                    import asyncio

                    await asyncio.sleep(0.6 * attempts)
                    continue
                raise
            except Exception as exc:
                if attempts <= 2:
                    import asyncio

                    await asyncio.sleep(0.6 * attempts)
                    continue
                raise ProviderError(
                    f"writer model failed for {purpose}: {exc.__class__.__name__}",
                    kind=FailureKind.NETWORK,
                    source="llm",
                ) from exc

    @staticmethod
    def _content(data: dict[str, Any]) -> str:
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderNoData("writer model returned no choices", source="llm")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise ProviderNoData("writer model returned no message", source="llm")
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not isinstance(content, str) or not content.strip():
            raise ProviderNoData("writer model returned empty content", source="llm")
        return content.strip()


class NullLLM:
    name = "offline"
    available = False

    async def complete(self, **_: Any) -> LLMResult:
        raise ProviderError("No writer model is configured", kind=FailureKind.AUTH, source="llm")
