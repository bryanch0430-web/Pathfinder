"""Screen fetched text in tool results before a model or the traveller sees it.

Proposal TC-05 (p4, l.149-154): probes may target "the model inside an agent", not
only the router, and the most direct way to reach it is text inside a tool
result (a web page, a place description, a community note). Every provider call
goes through the Toolbox, so the Toolbox passes each result through
``neutralise`` and any text field that reads as an instruction to a model is
replaced by a placeholder. Names, numbers and URLs are untouched.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any

from pydantic import BaseModel

from app.domain.models import Severity
from app.security.guard import INDIRECT_PATTERNS, PATTERNS

REMOVED = "[removed: instructions found in fetched text]"
#: high-severity user patterns, minus the bare word "canary" (Canary Wharf is a place) and
#: the bare "you are now" (replaced by a narrower role_override in INDIRECT_PATTERNS)
_PATTERNS = [
    (name, re.compile(pattern))
    for name, severity, pattern in PATTERNS
    if severity == Severity.HIGH and name not in {"canary_probe", "role_override"}
] + [(name, re.compile(pattern)) for name, pattern in INDIRECT_PATTERNS]
_MAX_DEPTH = 6


def screen_fetched_text(text: str) -> list[str]:
    if len(text) < 12:
        return []
    return sorted({name for name, pattern in _PATTERNS if pattern.search(text)})


def neutralise(value: Any, hits: list[str], _depth: int = 0) -> Any:
    """Return ``value`` with every instruction-like string replaced; ``hits`` collects categories."""
    if _depth > _MAX_DEPTH:
        return value
    if isinstance(value, str):
        found = screen_fetched_text(value)
        if found:
            hits.extend(found)
            return REMOVED
        return value
    if isinstance(value, list):
        for index, item in enumerate(value):
            value[index] = neutralise(item, hits, _depth + 1)
        return value
    if isinstance(value, tuple):
        return tuple(neutralise(item, hits, _depth + 1) for item in value)
    if isinstance(value, dict):
        for key in list(value):
            value[key] = neutralise(value[key], hits, _depth + 1)
        return value
    if isinstance(value, BaseModel):
        changes = {}
        for name in type(value).model_fields:
            current = getattr(value, name, None)
            updated = neutralise(current, hits, _depth + 1)
            if updated is not current:
                changes[name] = updated
        if changes:
            try:
                for name, updated in changes.items():
                    setattr(value, name, updated)
            except (TypeError, ValueError):
                value = value.model_copy(update=changes)
        return value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        changes = {}
        for item in dataclasses.fields(value):
            current = getattr(value, item.name, None)
            updated = neutralise(current, hits, _depth + 1)
            if updated is not current:
                changes[item.name] = updated
        if changes:
            try:
                for name, updated in changes.items():
                    setattr(value, name, updated)
            except dataclasses.FrozenInstanceError:
                value = dataclasses.replace(value, **changes)
        return value
    return value
