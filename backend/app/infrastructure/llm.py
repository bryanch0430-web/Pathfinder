"""OpenAI-compatible writer adapter (DeepSeek / xAI / any chat-completions API)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Sequence

import httpx

from app.config import Settings
from app.domain.errors import ProviderError, ProviderNoData
from app.domain.models import FailureKind
from app.domain.ports import ImageInput
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


def _user_content(user: str, images: Sequence[ImageInput] | None) -> Any:
    """The user turn: plain text, or text followed by the pictures as data URLs for a writer that can see."""
    if not images:
        return user
    return [
        {"type": "text", "text": user},
        *(
            {"type": "image_url", "image_url": {"url": f"data:{image.media_type};base64,{image.data}"}}
            for image in images
        ),
    ]


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
        fixed_temperature: float | None = None,
        fixed_max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
    ):
        self.settings = settings
        # One resolution point, shared with the container: Settings decides
        # which vendor label wins and therefore which URL/key/model is used.
        # An explicit argument (a user's own key) takes the place of the setting.
        self._base_url = settings.writer_base_url if base_url is None else base_url
        self._api_key = settings.writer_api_key if api_key is None else api_key
        # None follows the live writer model, so a settings change applies without a new process.
        self._pinned_model = model
        self._label = label
        self._fixed_temperature = fixed_temperature
        self._fixed_max_tokens = fixed_max_tokens
        self._extra = dict(extra or {})
        self.available = bool(self._base_url and self._api_key)

    @property
    def name(self) -> str:
        chosen = self._pinned_model if self._pinned_model is not None else self.settings.writer_model
        return (chosen or "").strip() or "offline"

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
        timeout: float | None = None,
        retries: int | None = None,
        reasoning: str | None = None,
    ) -> LLMResult:
        if not self.available:
            raise ProviderError("No writer model is configured", kind=FailureKind.AUTH, source="llm")
        if self._fixed_temperature is not None:
            temperature = self._fixed_temperature
        if self._fixed_max_tokens is not None:
            max_tokens = self._fixed_max_tokens
        payload: dict[str, Any] = {
            "model": self.name,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": _user_content(user, images)},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode and self.settings.llm_json_mode:
            payload["response_format"] = {"type": "json_object"}
        extra = dict(self._extra)
        effort = (reasoning or "").strip().lower()
        if effort in {"low", "medium", "high"}:
            extra["reasoning_effort"] = effort
        if extra:
            payload["extra_body"] = extra

        started = time.perf_counter()
        data = await self._post(payload, purpose, timeout=timeout, retries=retries)
        message = self._message(data)
        content = self._content(message)
        usage = data.get("usage") or {}
        return LLMResult(
            text=content,
            model=str(data.get("model") or self.name),
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            latency_ms=int((time.perf_counter() - started) * 1000),
            reasoning=self._reasoning(message),
        )

    async def _post(
        self,
        payload: dict[str, Any],
        purpose: str,
        *,
        timeout: float | None = None,
        retries: int | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        timeout = self.settings.llm_timeout_seconds if timeout is None else timeout
        # Counts only rate-limit, server-error and network retries. A field retry (a 400 naming a field we then
        # drop or rename) happens at most once per field and never uses up these retries.
        # `retries` is how many extra tries the caller allows; the writer keeps two.
        retry_limit = 2 if retries is None else max(0, retries)
        done = 0
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
                        if "extra_body" in payload and (
                            "extra_body" in body or "reasoning_effort" in body
                        ):
                            payload.pop("extra_body", None)
                            continue
                    detail = scrub(response.text, [self._api_key])[:300]
                    message = f"{self._label} returned HTTP {response.status_code}" if self._label else ""
                    raise classify_status(response.status_code, "llm", message, detail=detail)
                data = response.json()
                return data if isinstance(data, dict) else {"data": data}
            except ProviderError as exc:
                if exc.kind in {FailureKind.RATE_LIMIT, FailureKind.NETWORK, FailureKind.SERVER_ERROR} and done < retry_limit:
                    import asyncio

                    done += 1
                    await asyncio.sleep(0.6 * done)
                    continue
                raise
            except Exception as exc:
                if done < retry_limit:
                    import asyncio

                    done += 1
                    await asyncio.sleep(0.6 * done)
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
