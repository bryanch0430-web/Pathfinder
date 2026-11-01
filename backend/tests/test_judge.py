"""The optional judge parser: strict JSON, clamped score, no network."""

from __future__ import annotations

from app.config import Settings
from app.infrastructure.judge import Judge, JudgeVerdict, _parse_verdict


def test_judge_is_disabled_without_a_key() -> None:
    settings = Settings(_env_file=None, judge_api_key="")
    judge = Judge(settings)
    assert judge.available is False


def test_parse_plain_json() -> None:
    verdict = _parse_verdict('{"score": 4, "flags": ["tight day 2"], "reason": "Grounded and realistic."}')
    assert verdict is not None
    assert verdict.score == 4
    assert verdict.flags == ["tight day 2"]
    assert verdict.reason == "Grounded and realistic."


def test_parse_fenced_json() -> None:
    text = '```json\n{"score": 3, "flags": [], "reason": "Usable."}\n```'
    verdict = _parse_verdict(text)
    assert verdict is not None
    assert verdict.score == 3


def test_parse_clamps_out_of_range_score() -> None:
    assert _parse_verdict('{"score": 9}') == JudgeVerdict(score=5, flags=[], reason="")
    assert _parse_verdict('{"score": 0}') == JudgeVerdict(score=1, flags=[], reason="")


def test_parse_rejects_non_json() -> None:
    assert _parse_verdict("looks fine to me") is None
    assert _parse_verdict('{"score": "high"}') is None
