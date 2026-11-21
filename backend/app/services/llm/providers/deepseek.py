"""DeepSeek Chat Completions using the project's existing HTTP client dependency."""

import json
from typing import Any, Optional

import httpx

from app.core.config import settings
from app.services.llm.base import LLMProvider, LLMResult, ToolCall


class DeepSeekProvider(LLMProvider):
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self._api_key = settings.DEEPSEEK_API_KEY if api_key is None else api_key
        self._model_name = model or settings.DEEPSEEK_MODEL

    def _get_client(self) -> httpx.AsyncClient:
        if not self._api_key:
            raise ValueError("DEEPSEEK_API_KEY is not configured.")
        return httpx.AsyncClient(
            base_url=settings.DEEPSEEK_BASE_URL.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=httpx.Timeout(120.0, connect=15.0),
        )

    def _payload(self, prompt, system_instruction, temperature, max_output_tokens, **kwargs):
        messages = []
        if system_instruction and system_instruction.strip():
            messages.append({"role": "system", "content": system_instruction.strip()})
        messages.append({"role": "user", "content": prompt})
        effort = kwargs.pop("reasoning_effort", None)
        if effort == "xhigh" or effort == "medium":
            effort = "high"
        if effort not in {"low", "high", "max"}:
            effort = None
        payload = {
            "model": self._model_name,
            "messages": messages,
            "temperature": temperature,
        }
        if effort:
            payload["reasoning_effort"] = effort
        else:
            payload["thinking"] = {"type": "disabled"}
        if max_output_tokens is not None:
            payload["max_tokens"] = max_output_tokens
        payload.update(kwargs)
        return payload

    async def _complete(self, payload: dict) -> dict:
        async with self._get_client() as client:
            response = await client.post("chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
        if "error" in data:
            raise RuntimeError("DeepSeek returned an API error.")
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ValueError("DeepSeek response exceeded the output token budget.")
        return choice["message"]

    async def generate(
        self, prompt: str, system_instruction: Optional[str] = None,
        temperature: float = 0.7, max_output_tokens: Optional[int] = None, **kwargs: Any,
    ) -> str:
        message = await self._complete(self._payload(
            prompt, system_instruction, temperature, max_output_tokens, **kwargs,
        ))
        return (message.get("content") or "").strip()

    async def generate_with_tools(
        self, prompt: str, system_instruction: Optional[str] = None,
        tools: Optional[list[Any]] = None, temperature: float = 0.7,
        max_output_tokens: Optional[int] = None, **kwargs: Any,
    ) -> LLMResult:
        payload = self._payload(prompt, system_instruction, temperature, max_output_tokens, **kwargs)
        converted = []
        for tool in tools or []:
            if isinstance(tool, dict):
                converted.append(tool if "function" in tool else {"type": "function", "function": tool})
            elif callable(tool):
                # The planner currently supplies only the no-argument location function.
                converted.append({"type": "function", "function": {
                    "name": tool.__name__, "description": (tool.__doc__ or tool.__name__).strip(),
                    "parameters": {"type": "object", "properties": {}, "required": []},
                }})
            else:
                raise TypeError("Unsupported DeepSeek tool definition.")
        if converted:
            payload["tools"] = converted
            payload.setdefault("tool_choice", "auto")
        message = await self._complete(payload)
        calls = []
        for tool_call in message.get("tool_calls") or []:
            function = tool_call["function"]
            arguments = json.loads(function.get("arguments") or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("DeepSeek tool arguments must be a JSON object.")
            calls.append(ToolCall(name=function["name"], args=arguments))
        return LLMResult(text=(message.get("content") or "").strip(), tool_calls=calls)

    async def generate_stream(
        self, prompt: str, system_instruction: Optional[str] = None,
        temperature: float = 0.7, max_output_tokens: Optional[int] = None, **kwargs: Any,
    ):
        payload = self._payload(prompt, system_instruction, temperature, max_output_tokens, **kwargs)
        payload["stream"] = True
        async with self._get_client() as client:
            async with client.stream("POST", "chat/completions", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = line[5:].strip()
                    if event == "[DONE]":
                        return
                    data = json.loads(event)
                    if "error" in data:
                        raise RuntimeError("DeepSeek returned a streaming API error.")
                    for choice in data.get("choices", []):
                        if choice.get("finish_reason") == "length":
                            raise ValueError("DeepSeek response exceeded the output token budget.")
                        content = choice.get("delta", {}).get("content")
                        if content:
                            yield content
        raise RuntimeError("DeepSeek stream ended before its completion marker.")
