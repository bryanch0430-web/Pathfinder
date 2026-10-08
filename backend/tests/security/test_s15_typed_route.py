"""Scenario 15: the router can only emit a typed route; free text is rejected."""

from __future__ import annotations

import json

import pytest

from backend.schemas.common import AgentName, Route
from backend.security.routes import UntypedRouteError, parse_router_output

VALID = {"route": "plan", "confidence": 0.93, "clarity": 0.8, "needs_clarification": False}


def test_s15_valid_json_decision_parses() -> None:
    decision = parse_router_output(json.dumps(VALID))
    assert decision.route is Route.PLAN
    assert decision.confidence == pytest.approx(0.93)
    assert decision.needs_clarification is False
    assert decision.affected_parts == []


def test_s15_surrounding_whitespace_is_stripped() -> None:
    assert parse_router_output("\n  " + json.dumps(VALID) + " \r\n").route is Route.PLAN


def test_s15_modify_with_affected_parts() -> None:
    raw = json.dumps({**VALID, "route": "modify", "affected_parts": ["hotel", "weather"]})
    decision = parse_router_output(raw)
    assert decision.route is Route.MODIFY
    assert decision.affected_parts == [AgentName.HOTEL, AgentName.WEATHER]


@pytest.mark.parametrize("route", [r.value for r in Route])
def test_s15_every_route_enum_value_is_accepted(route: str) -> None:
    assert parse_router_output(json.dumps({**VALID, "route": route})).route.value == route


@pytest.mark.parametrize(
    "raw",
    [
        "plan",  # bare free text
        "Sure! route=plan",  # prose
        "I think this is a plan request.",
        "```json\n" + json.dumps(VALID) + "\n```",  # markdown fence
        "Here is the decision: " + json.dumps(VALID),  # prose before
        json.dumps(VALID) + " Hope that helps!",  # prose after
        json.dumps({**VALID, "reply": "hello there"}),  # extra key
        json.dumps({**VALID, "route": "book"}),  # unknown route
        json.dumps({**VALID, "route": "PLAN"}),  # enum is case-sensitive
        json.dumps([VALID]),  # array
        json.dumps(VALID) + json.dumps(VALID),  # two objects
        json.dumps(VALID) + "\n" + json.dumps({**VALID, "route": "ask"}),  # two objects
        '{"route": "plan", "route": "ask", "confidence": 1, "clarity": 1, "needs_clarification": false}',
        "",  # empty
        "   ",
        "{}",  # missing fields
        '{"route": "plan"}',
        "{not json}",
        json.dumps({**VALID, "confidence": 1.5}),  # out of range
        json.dumps({**VALID, "confidence": "0.9"}),  # strings are not coerced
        json.dumps({**VALID, "needs_clarification": "yes"}),
        '{"route": "plan", "confidence": NaN, "clarity": 1, "needs_clarification": false}',
        json.dumps({**VALID, "route": None}),
        "null",
        '"plan"',
        "{" + "[" * 5000 + "}",  # pathological nesting
    ],
)
def test_s15_untyped_output_is_rejected(raw: str) -> None:
    with pytest.raises(UntypedRouteError):
        parse_router_output(raw)


def test_s15_error_chains_cause_and_does_not_echo_raw_text() -> None:
    secret = "SECRET-INJECTED-TEXT-12345"
    with pytest.raises(UntypedRouteError) as info:
        parse_router_output(json.dumps({**VALID, "route": "plan", "reply": secret}))
    assert info.value.__cause__ is not None
    assert secret not in str(info.value)

    with pytest.raises(UntypedRouteError) as info2:
        parse_router_output("{" + secret + "}")
    assert info2.value.__cause__ is not None
    assert secret not in str(info2.value)


def test_s15_untyped_route_error_is_a_value_error() -> None:
    assert issubclass(UntypedRouteError, ValueError)
