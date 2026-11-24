"""Canonical names for the model parts shown in Settings.

The UI says Analyst, Planner, Editor, Verifier, Ask and Memory. The backend
uses the same names now; the pre-rename keys stay accepted as aliases so
existing `.env` files, saved runtime overrides and scripts keep working.
"""

from __future__ import annotations

from typing import Any, Mapping

# old key -> the name the UI and the runtime use now
_ALIASES = {
    "understand": "analyst",
    "plan": "planner",
    "edit": "editor",
    "verify": "verifier",
    "translate": "translation",
}

CANONICAL_PURPOSES = (
    "analyst",
    "planner",
    "editor",
    "verifier",
    "ask",
    "memory",
    "intake",
    "translation",
    "search_query",
)


def canonical_purpose(name: Any) -> str:
    text = str(name or "").strip().lower()
    return _ALIASES.get(text, text)


def aliases_for(name: str) -> tuple[str, ...]:
    canonical = canonical_purpose(name)
    return tuple(old for old, new in _ALIASES.items() if new == canonical)


def purpose_value(mapping: Mapping[str, Any] | None, name: str) -> Any:
    """Read a configured value by canonical or legacy key."""
    if not mapping:
        return None
    canonical = canonical_purpose(name)
    if canonical in mapping:
        return mapping[canonical]
    for old in aliases_for(canonical):
        if old in mapping:
            return mapping[old]
    return None


def normalise_purpose_map(mapping: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a copy keyed by the canonical names; later entries win."""
    return {canonical_purpose(key): value for key, value in (mapping or {}).items()}
