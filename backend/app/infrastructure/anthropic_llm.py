"""Anthropic Messages API adapter. No vendor SDK, no hidden prompt."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Sequence

import httpx

from app.config import Settings
from app.domain.errors import ProviderError, ProviderNoData
from app.domain.models import FailureKind
from app.domain.ports import ImageInput
from app.infrastructure.http import classify_status, current_transport
from app.infrastructure.llm import LLMResult
from app.security.scrub import scrub

ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
JSON_ONLY_INSTRUCTION = "\n\nRespond with a single valid JSON object only. No prose and no code fences."


def _user_content(user: str, images: Sequence[ImageInput] | None) -> Any:
    """The user turn: plain text, or the pictures as image blocks followed by the text."""
    if not images:
        return user
    return [
        *(
            {"type": "image", "source": {"type": "base64", "media_type": image.media_type, "data": image.data}}
            for image in images
        ),
        {"type": "text", "text": user},
    ]


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, settings: Settings, *, api_key: str, model: str):
        self.settings = settings
        self._api_key = api_key
        self._model = model
        self.available = bool(api_key and model)
        #: Set once the model refuses `temperature` (newer Claude models do): later calls leave it out.
        self._no_temperature = False
        self.name = model or "offline"

    async def complete(
        self,
        *,
        system: str,
        user: str,
        purpose: str,
        json_mode: bool = True,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        images: Sequence[ImageInput] | None = None,
    ) -> LLMResult:
        if not self.available:
            raise ProviderError("No writer model is configured", kind=FailureKind.AUTH, source="llm")
        payload: dict[str, Any] = {
            "model": self._model,
            "system": system + JSON_ONLY_INSTRUCTION if json_mode else system,
            "messages": [{"role": "user", "content": _user_content(user, images)}],
            "max_tokens": max_tokens,
        }
        if not self._no_temperature:
            payload["temperature"] = temperature
        started = time.perf_counter()
        data = await self._post(payload, purpose)
        text, reasoning = self._blocks(data)
        usage = data.get("usage") or {}
        return LLMResult(
            text=text,
            model=str(data.get("model") or self._model),
            prompt_tokens=int(usage.get("input_tokens") or 0),
            completion_tokens=int(usage.get("output_tokens") or 0),
            latency_ms=int((time.perf_counter() - started) * 1000),
            reasoning=reasoning,
        )

    async def _post(self, payload: dict[str, Any], purpose: str) -> dict[str, Any]:
        url = f"{ANTHROPIC_BASE_URL}/messages"
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        timeout = self.settings.llm_timeout_seconds
        attempts = 0
        while True:
            attempts += 1
            try:
                async with httpx.AsyncClient(timeout=timeout, transport=current_transport()) as client:
                    response = await client.post(url, headers=headers, json=payload)
                if response.status_code == 400 and "temperature" in payload and "temperature" in response.text:
                    # Newer Claude models refuse the field. Send the same request once more without it; this
                    # is not one of the retries below.
                    payload.pop("temperature")
                    self._no_temperature = True
                    attempts -= 1
                    continue
                if response.status_code >= 400:
                    detail = scrub(response.text, [self._api_key])[:300]
                    raise classify_status(
                        response.status_code, "llm", f"Anthropic Claude returned HTTP {response.status_code}", detail=detail
                    )
                data = response.json()
                return data if isinstance(data, dict) else {"data": data}
            except ProviderError as exc:
                if exc.kind in {FailureKind.RATE_LIMIT, FailureKind.NETWORK, FailureKind.SERVER_ERROR} and attempts <= 2:
                    await asyncio.sleep(0.6 * attempts)
                    continue
                raise
            except Exception as exc:
                if attempts <= 2:
                    await asyncio.sleep(0.6 * attempts)
                    continue
                raise ProviderError(
                    f"writer model failed for {purpose}: {exc.__class__.__name__}",
                    kind=FailureKind.NETWORK,
                    source="llm",
                ) from exc

    @staticmethod
    def _blocks(data: dict[str, Any]) -> tuple[str, str]:
        """The answer (text blocks) and the reasoning (thinking blocks) kept apart."""
        content = data.get("content")
        if not isinstance(content, list):
            raise ProviderNoData("writer model returned no content", source="llm")
        text = "".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text").strip()
        if not text:
            raise ProviderNoData("writer model returned empty content", source="llm")
        thinking = "".join(
            b.get("thinking", "") for b in content if isinstance(b, dict) and b.get("type") == "thinking"
        )
        return text, thinking.strip()[:4000]
