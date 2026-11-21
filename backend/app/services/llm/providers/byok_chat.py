"""OpenAI-compatible chat for a bring-your-own key. Poe and xAI both speak this.

Grok 4.7 is called on the Responses API. High reasoning is requested explicitly.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Optional

import httpx

from app.services.llm.base import LLMProvider, LLMResult


class ByokChatProvider(LLMProvider):
    def __init__(
        self, *, api_key: str, base_url: str, model: str, wire: str = "openai",
        sampling_temperature: str | None = None, sampling_reasoning: str | None = None,
    ):
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._wire = wire
        self.sampling_temperature = sampling_temperature or None
        self.sampling_reasoning = sampling_reasoning or None

    def _grok_47(self) -> bool:
        return self._model.startswith("grok-4.7")

    def _responses_body(self, prompt: str, system_instruction: str | None, max_output_tokens: Optional[int]) -> dict:
        messages: list[dict[str, str]] = []
        if system_instruction and system_instruction.strip():
            messages.append({"role": "system", "content": system_instruction.strip()})
        messages.append({"role": "user", "content": prompt})
        effort = self.sampling_reasoning or "high"
        if effort not in {"low", "medium", "high", "xhigh"}:
            effort = "high"
        body: dict[str, Any] = {
            "model": self._model,
            "input": messages,
            "reasoning": {"effort": effort},
        }
        if self.sampling_temperature:
            body["temperature"] = float(self.sampling_temperature)
        if max_output_tokens is not None:
            body["max_output_tokens"] = max_output_tokens
        return body

    @staticmethod
    def _response_text(data: dict) -> str:
        direct = data.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        parts: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if isinstance(content, dict) and content.get("type") == "output_text" and content.get("text"):
                    parts.append(content["text"])
        return "".join(parts).strip()

    def _openai_payload(self, prompt: str, system_instruction: str | None, temperature: float, stream: bool) -> dict:
        messages = []
        if system_instruction and system_instruction.strip():
            messages.append({"role": "system", "content": system_instruction.strip()})
        messages.append({"role": "user", "content": prompt})
        payload: dict[str, Any] = {
            "model": self._model,
            "temperature": temperature,
            "messages": messages,
        }
        if stream:
            payload["stream"] = True
        effort = self._wire_effort()
        if "deepseek" in self._base_url and effort is None:
            payload["thinking"] = {"type": "disabled"}
        elif effort and "deepseek" in self._base_url:
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = effort
        elif effort:
            payload["reasoning_effort"] = effort
        return payload

    def _wire_effort(self) -> str | None:
        effort = self.sampling_reasoning
        if not effort or effort == "none":
            return None
        if "deepseek" in self._base_url:
            if effort in {"medium", "xhigh"}:
                return "high"
            return effort if effort in {"low", "high", "max"} else None
        if self._model.startswith("grok-"):
            return effort if effort in {"low", "medium", "high", "xhigh"} else None
        if "claude" in self._model.casefold():
            return effort if effort in {"low", "medium", "high", "xhigh", "max"} else None
        if "gemini" in self._model.casefold() or "googleapis" in self._base_url:
            return effort if effort in {"low", "medium", "high"} else None
        if any(token in self._model.casefold() for token in ("gpt-5", "gpt-6", "o1", "o3", "o4")):
            return effort if effort in {"low", "medium", "high"} else None
        return None

    async def generate(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        if self._wire == "anthropic":
            return await self._anthropic(prompt, system_instruction, temperature, max_output_tokens)
        if self._grok_47():
            return await self._grok_responses(prompt, system_instruction, max_output_tokens)
        payload = self._openai_payload(prompt, system_instruction, temperature, False)
        if max_output_tokens is not None:
            payload["max_tokens"] = max_output_tokens
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        return (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()

    async def _anthropic(self, prompt, system_instruction, temperature, max_output_tokens) -> str:
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_output_tokens or 2048,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_instruction:
            body["system"] = system_instruction
        effort = self._wire_effort()
        if effort:
            body["output_config"] = {"effort": effort}
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                f"{self._base_url}/messages",
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json=body,
            )
            response.raise_for_status()
            data = response.json()
        parts = data.get("content") or []
        return "".join(part.get("text", "") for part in parts if isinstance(part, dict)).strip()

    async def _grok_responses(self, prompt: str, system_instruction: str | None, max_output_tokens: Optional[int]) -> str:
        async with httpx.AsyncClient(timeout=300) as client:
            response = await client.post(
                f"{self._base_url}/responses",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=self._responses_body(prompt, system_instruction, max_output_tokens),
            )
            if response.status_code >= 400:
                detail = ""
                try:
                    payload = response.json()
                    detail = str(payload.get("error") or payload.get("code") or "")
                except Exception:
                    detail = ""
                if self._api_key and self._api_key in detail:
                    detail = detail.replace(self._api_key, "")
                raise RuntimeError(f"Grok rejected the request ({response.status_code}): {detail[:180]}")
            data = response.json()
        return self._response_text(data)

    async def generate_stream(
        self,
        prompt: str,
        system_instruction: Optional[str] = None,
        temperature: float = 0.7,
        max_output_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        if self._wire == "anthropic":
            yield await self._anthropic(prompt, system_instruction, temperature, max_output_tokens)
            return
        if self._grok_47():
            yield await self._grok_responses(prompt, system_instruction, max_output_tokens)
            return
        payload = self._openai_payload(prompt, system_instruction, temperature, True)
        if max_output_tokens is not None:
            payload["max_tokens"] = max_output_tokens
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST",
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    delta = ((chunk.get("choices") or [{}])[0].get("delta") or {}).get("content")
                    if delta:
                        yield delta

    async def generate_with_tools(self, prompt: str, system_instruction: Optional[str] = None, tools=None, temperature: float = 0.7, max_output_tokens: Optional[int] = None, **kwargs: Any) -> LLMResult:
        text = await self.generate(prompt, system_instruction, temperature, max_output_tokens)
        return LLMResult(text=text)
