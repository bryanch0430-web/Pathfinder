"""One guarded path to every writer-model call: trace, parse, canary check."""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError as PydanticValidationError

from app.application.prompts import PromptLibrary
from app.config import Settings
from app.domain.errors import CanaryLeak, ProviderError
from app.domain.ports import LLM, Tracer
from app.security import canary_leaked

T = TypeVar("T", bound=BaseModel)


class LLMGateway:
    def __init__(self, llm: LLM, prompts: PromptLibrary, tracer: Tracer, settings: Settings):
        self.llm = llm
        self.prompts = prompts
        self.tracer = tracer
        self.settings = settings
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0

    @property
    def available(self) -> bool:
        return bool(getattr(self.llm, "available", False))

    async def json(
        self,
        prompt: str,
        *,
        user: str,
        schema: type[T] | None = None,
        purpose: str | None = None,
    ) -> dict[str, Any]:
        result = await self._call(prompt, user=user, purpose=purpose or prompt, json_mode=True)
        data = _extract_json(result)
        if data is None:
            # One bounded repair attempt: ask for JSON only, then parse again.
            result = await self._call(
                prompt,
                user=user + "\n\nYour previous answer was not valid JSON. Return the JSON object only.",
                purpose=f"{purpose or prompt}.repair",
                json_mode=True,
            )
            data = _extract_json(result)
        if data is None:
            raise ProviderError(
                f"writer returned invalid JSON for {purpose or prompt}",
                source="llm",
            )
        if schema is not None:
            try:
                return schema.model_validate(data).model_dump()
            except PydanticValidationError as exc:
                raise ProviderError(
                    f"writer returned JSON that does not match {schema.__name__}: {exc.errors()[:2]}",
                    source="llm",
                ) from exc
        return data

    async def text(self, prompt: str, *, user: str, purpose: str | None = None) -> str:
        return await self._call(prompt, user=user, purpose=purpose or prompt, json_mode=False)

    async def _call(self, prompt: str, *, user: str, purpose: str, json_mode: bool) -> str:
        system = self.prompts.system(prompt)
        with self.tracer.span(f"llm.{purpose}") as span:
            result = await self.llm.complete(
                system=system,
                user=user,
                purpose=purpose,
                json_mode=json_mode,
                temperature=self.settings.llm_temperature,
                max_tokens=self.settings.llm_max_tokens,
            )
            text = result.text
            if canary_leaked(text, self.settings):
                raise CanaryLeak("writer output contained the canary token")
            self.prompt_tokens += result.prompt_tokens
            self.completion_tokens += result.completion_tokens
            self.calls += 1
            self.tracer.record_generation(
                purpose=purpose,
                model=result.model,
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                latency_ms=result.latency_ms,
                output_preview=text,
            )
            span.attrs.update({"purpose": purpose, "model": result.model})
            return text


def _extract_json(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            data = json.loads(cleaned[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None
