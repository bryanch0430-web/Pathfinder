"""Prompt-scaffold ablation: full `understand` skill vs a minimal router prompt.

This is deliberately a scaffold ablation, not a claim that every sentence of the
skill is necessary. The minimal condition receives the same model, temperature,
JSON schema and user payload; only the domain instructions are replaced.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PATHFINDER_SANDBOX", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.llm import LLMClient  # noqa: E402
from app.core.trace import TurnTrace  # noqa: E402
from app.domain.models import TripContext  # noqa: E402
from app.eval import datasets  # noqa: E402
from app.understand.pipeline import understand  # noqa: E402

OUT = Path(__file__).resolve().parent.parent.parent / "docs" / "eval-artifacts" / "v5-model-study"

MINIMAL = """You are a message router. Read the traveller message and return a JSON object
that matches this schema:
""" + json.dumps(__import__("app.understand.schemas", fromlist=["ParsedIntent"]).ParsedIntent.model_json_schema()) + """

Choose exactly one intent: plan, modify, ask, or unclear. Do not invent trip details.
Keep the reason short."""


async def one(settings, case, condition: str, semaphore: asyncio.Semaphore) -> dict:
    async with semaphore:
        trace = TurnTrace(request_id=f"skill-{condition}-{case.id}")
        llm = LLMClient(settings, trace=trace)
        started = time.perf_counter()
        override = {"understand": MINIMAL} if condition == "minimal" else None
        try:
            result = await understand(
                llm=llm,
                settings=settings,
                context=TripContext(
                    session_id=f"skill-{case.id}",
                    destination=case.destination,
                    origin=case.origin,
                    currency=case.currency or settings.default_currency,
                    language="en",
                ),
                plan=None,
                messages=[],
                message=case.input,
                scope_label=None,
                scope_item_id=None,
                locale="en",
                skill_overrides=override,
            )
            predicted = result.parsed.intent
            confidence = result.parsed.confidence
            error = ""
        except Exception as exc:  # noqa: BLE001
            predicted, confidence, error = "error", None, str(exc)[:240]
        finally:
            await llm.aclose()
        return {
            "id": case.id,
            "input": case.input,
            "expected": case.expected_intent,
            "predicted": predicted,
            "correct": predicted == case.expected_intent,
            "confidence": round(float(confidence), 3) if confidence is not None else None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "tokens": trace.total_tokens,
            "error": error,
        }


async def main() -> None:
    model = os.environ.get("SKILL_MODEL", "deepseek-flash")
    settings = get_settings().model_copy(
        update={"purpose_models": {"understand": model}, "purpose_temperatures": {"understand": 0.0}}
    )
    cases = datasets.load("routing")[:24]
    semaphore = asyncio.Semaphore(int(os.environ.get("BENCH_CONCURRENCY", "1")))
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "model": model, "temperature": 0.0, "n": len(cases), "arms": {}}
    for condition in ("full", "minimal"):
        print(f"condition {condition}: {len(cases)} cases", flush=True)
        rows = await asyncio.gather(*(one(settings, case, condition, semaphore) for case in cases))
        correct = sum(1 for row in rows if row["correct"])
        latencies = [row["latency_ms"] for row in rows]
        tokens = [row["tokens"] for row in rows]
        by_intent = {}
        for row in rows:
            bucket = by_intent.setdefault(row["expected"], {"n": 0, "correct": 0})
            bucket["n"] += 1
            bucket["correct"] += int(row["correct"])
        payload["arms"][condition] = {
            "condition": condition,
            "correct": correct,
            "n": len(rows),
            "accuracy": round(correct / len(rows), 4),
            "p50_latency_ms": round(statistics.median(latencies), 1),
            "mean_tokens": round(sum(tokens) / len(tokens), 1),
            "by_intent": by_intent,
            "rows": rows,
        }
        print(f"  {condition}: {correct}/{len(rows)}", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    (OUT / f"skills-ablation-{stamp}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "skills-ablation-latest.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    a = payload["arms"]["full"]
    b = payload["arms"]["minimal"]
    lines = [
        "# Prompt-scaffold ablation (full skill vs minimal router)",
        "",
        f"Model: `{payload['model']}`, temperature: {payload['temperature']}, n={payload['n']}.",
        "",
        "| Condition | Correct | Accuracy | p50 | Mean tokens |",
        "|---|---:|---:|---:|---:|",
        f"| full skill | {a['correct']}/{a['n']} | {a['accuracy']:.1%} | {a['p50_latency_ms']} ms | {a['mean_tokens']} |",
        f"| minimal prompt | {b['correct']}/{b['n']} | {b['accuracy']:.1%} | {b['p50_latency_ms']} ms | {b['mean_tokens']} |",
        "",
        "The minimal condition preserves the same model, temperature, schema and payload but removes the domain guidance.",
    ]
    (OUT / "skills-ablation-latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
