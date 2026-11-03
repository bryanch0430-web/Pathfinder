"""The Langfuse sink writes OTLP JSON; ids and timestamps must match the spec."""

from __future__ import annotations

import json
from pathlib import Path

from app.application.orchestrator import _note_failure
from app.config import Settings
from app.domain.errors import ProviderError
from app.infrastructure.observability import TraceRecorder, _otlp_payload


def _record() -> dict:
    return {
        "request_id": "req_otlp_test",
        "status": "ok",
        "started_at": "2026-11-01T00:00:00+00:00",
        "ended_at": "2026-11-01T00:00:01+00:00",
        "duration_ms": 1000,
        "metadata": {"intent": "plan"},
        "spans": [
            {
                "id": "span_abc",
                "name": "geocode",
                "started_at": "2026-11-01T00:00:00.100000+00:00",
                "ended_at": "2026-11-01T00:00:00.400000+00:00",
                "attrs": {"destination": "Kyoto", "count": 2},
            }
        ],
        "generations": [
            {
                "id": "gen_abc",
                "purpose": "understand",
                "model": "test-model",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "latency_ms": 200,
                "at": "2026-11-01T00:00:00.500000+00:00",
            }
        ],
    }


def test_otlp_payload_has_hex_ids_and_all_observations() -> None:
    payload = _otlp_payload(_record())
    spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]

    assert [span["name"] for span in spans] == ["pathfinder.turn", "geocode", "understand"]
    for span in spans:
        assert len(span["traceId"]) == 32
        assert all(char in "0123456789abcdef" for char in span["traceId"])
        assert len(span["spanId"]) == 16
        assert int(span["endTimeUnixNano"]) >= int(span["startTimeUnixNano"])

    assert spans[1]["parentSpanId"] == spans[0]["spanId"]
    assert spans[2]["parentSpanId"] == spans[0]["spanId"]


def test_generation_is_marked_for_langfuse() -> None:
    payload = _otlp_payload(_record())
    generation = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][2]
    attributes = {item["key"]: item["value"] for item in generation["attributes"]}
    assert attributes["langfuse.observation.type"]["stringValue"] == "generation"
    assert attributes["gen_ai.usage.input_tokens"]["intValue"] == "10"
    assert attributes["gen_ai.usage.output_tokens"]["intValue"] == "5"


def test_the_text_of_an_exception_stays_in_the_local_trace_and_is_not_exported(tmp_path: Path) -> None:
    """LEAK-G10: the turn.failure span keeps the exception's text for the operator; Langfuse gets its class."""
    settings = Settings(_env_file=None, environment="test", trace_dir=tmp_path)
    tracer = TraceRecorder("req_leak_g10", settings)
    failure = ProviderError(
        "The map source failed.", detail="GET https://maps.example/geocode?key=SECRET-KEY&address=12+Elm+Street"
    )

    _note_failure(tracer, failure)
    tracer.finish(status="error")

    local = json.loads((tmp_path / "req_leak_g10.json").read_text(encoding="utf-8"))
    exported = json.dumps(_otlp_payload(local))
    assert "SECRET-KEY" in json.dumps(local) and "12+Elm+Street" in json.dumps(local)
    assert "SECRET-KEY" not in exported and "Elm" not in exported and "The map source failed" not in exported
    assert "ProviderError" in exported and "turn.failure" in exported


def test_only_the_exception_text_is_left_out_of_a_spans_exported_attributes() -> None:
    record = _record()
    record["spans"][0]["attrs"] = {
        "destination": "Kyoto",
        "error_class": "TimeoutError",
        "error": "took too long for 12 Elm Street",
    }

    spans = _otlp_payload(record)["resourceSpans"][0]["scopeSpans"][0]["spans"]
    attributes = {item["key"]: item["value"]["stringValue"] for item in spans[1]["attributes"]}

    assert attributes == {"destination": "Kyoto", "error_class": "TimeoutError"}
