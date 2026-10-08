"""Typed-route enforcement.

Proposal: "Jev can return only a typed route". The router output is accepted only if it is a
single JSON object that validates as `RouterDecision` (route in the Route enum, extra keys
forbidden). Free text, prose around JSON, markdown fences, multiple objects, or an unknown route
all raise UntypedRouteError. The point: even a fully hijacked router has no channel to emit
instructions, tool calls or prose; the worst it can do is pick one of four routes.

Validation is strict (JSON mode, no coercion): "confidence": "0.9" or "needs_clarification":
"yes" are rejected rather than quietly converted.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from backend.schemas.routing import RouterDecision


class UntypedRouteError(ValueError):
    """The router produced something other than a typed route."""


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(name: str) -> object:
    raise ValueError(f"non-finite number {name}")


def parse_router_output(raw: str) -> RouterDecision:
    """Return the `RouterDecision` encoded by `raw`, or raise `UntypedRouteError`.

    Only surrounding whitespace is stripped. The text must then start with `{`, end with `}`
    and be exactly one JSON object with no duplicate keys. The error message never echoes the
    raw text (it is untrusted and may contain the injection being rejected).
    """
    if not isinstance(raw, str):
        raise UntypedRouteError("router output is not text")
    text = raw.strip()
    if not (text.startswith("{") and text.endswith("}")):
        raise UntypedRouteError("router output is not a single JSON object")
    try:
        parsed = json.loads(
            text, object_pairs_hook=_no_duplicate_keys, parse_constant=_reject_constant
        )
    except (ValueError, RecursionError) as exc:  # JSONDecodeError is a ValueError
        raise UntypedRouteError(f"router output is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise UntypedRouteError("router output is not a JSON object")
    try:
        return RouterDecision.model_validate_json(text, strict=True)
    except ValidationError as exc:
        names = {".".join(str(part) for part in err["loc"]) or "<root>" for err in exc.errors()}
        fields = ", ".join(sorted(names))[:200]
        raise UntypedRouteError(f"router output is not a valid RouterDecision ({fields})") from exc
