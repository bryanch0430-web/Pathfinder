"""The Langfuse sink writes OTLP JSON; ids and timestamps must match the spec."""

from __future__ import annotations

from app.infrastructure.observability import _otlp_payload


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
