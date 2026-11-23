"""Auto-test the routing suite through ChatAnywhere and Jev.

This is real execution, not a probe. It writes case-level results under
docs/eval-artifacts/autotest/. No API key is written to the result.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.llm import LLMClient  # noqa: E402
from app.core.trace import TurnTrace  # noqa: E402
from app.domain.models import ChatMessage, TripContext  # noqa: E402
from app.eval import datasets  # noqa: E402
from app.understand.jev import jev_route  # noqa: E402
from app.understand.pipeline import understand  # noqa: E402

OUT = PROJECT / "docs" / "eval-artifacts" / "autotest"
ENV = PROJECT / ".env.autotest"


def env_values() -> dict[str, str]:
    values = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def wilson(passed: int, total: int) -> tuple[float, float]:
    if not total:
        return 0.0, 0.0
    z = 1.96
    p = passed / total
    den = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / den
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / den
    return max(0.0, centre - margin), min(1.0, centre + margin)


async def llm_case(settings, case, semaphore):
    async with semaphore:
        trace = TurnTrace(request_id=f"routing-{case.id}")
        llm = LLMClient(settings, trace=trace)
        started = time.perf_counter()
        try:
            result = await understand(
                llm=llm,
                settings=settings,
                context=TripContext(
                    session_id=f"routing-{case.id}",
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
            )
            intent = result.parsed.intent
            confidence = result.parsed.confidence
            error = ""
        except Exception as exc:  # noqa: BLE001
            intent, confidence, error = "error", None, str(exc)[:300]
        finally:
            await llm.aclose()
        return {
            "id": case.id,
            "input": case.input,
            "expected": case.expected_intent,
            "predicted": intent,
            "correct": intent == case.expected_intent,
            "confidence": round(float(confidence), 3) if confidence is not None else None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "tokens": trace.total_tokens,
            "error": error,
        }


async def jev_case(settings, case, semaphore):
    async with semaphore:
        started = time.perf_counter()
        try:
            decision = await jev_route(
                settings=settings,
                context=TripContext(
                    session_id=f"jev-{case.id}",
                    destination=case.destination,
                    origin=case.origin,
                    currency=case.currency or settings.default_currency,
                ),
                plan=None,
                message=case.input,
                history=[],
            )
            intent = decision.intent if decision else "error"
            confidence = decision.confidence if decision else None
            error = "" if decision else "no decision"
        except Exception as exc:  # noqa: BLE001
            intent, confidence, error = "error", None, str(exc)[:300]
        return {
            "id": case.id,
            "input": case.input,
            "expected": case.expected_intent,
            "predicted": intent,
            "correct": intent == case.expected_intent,
            "confidence": round(float(confidence), 3) if confidence is not None else None,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "tokens": None,
            "error": error,
        }


async def main():
    env = env_values()
    base_url = env.get("AUTOTEST_BASE_URL", "https://api.chatanywhere.org/v1")
    key = env.get("AUTOTEST_API_KEY", "")
    jev_key = env.get("JEV_API_KEY", "")
    if not key:
        raise SystemExit("AUTOTEST_API_KEY missing from .env.autotest")
    models = [m.strip() for m in env.get("AUTOTEST_MODELS", "").split(",") if m.strip()]
    cases = datasets.load("routing")
    concurrency = int(os.environ.get("AUTOTEST_CONCURRENCY", "2"))
    semaphore = asyncio.Semaphore(concurrency)
    base = get_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "suite": "routing", "n": len(cases), "base_url": base_url, "temperature": 0.0, "arms": {}}

    for model in models:
        settings = base.model_copy(
            update={
                "llm_provider": "openai",
                "openai_base_url": base_url,
                "openai_api_key": key,
                "openai_model": model,
                "llm_model": model,
                "purpose_models": {"understand": model},
                "purpose_temperatures": {"understand": 0.0},
                "llm_temperature": 0.0,
            }
        )
        print(f"== {model} ==", flush=True)
        rows = await asyncio.gather(*(llm_case(settings, case, semaphore) for case in cases))
        passed = sum(1 for row in rows if row["correct"])
        latencies = [row["latency_ms"] for row in rows]
        tokens = [row["tokens"] for row in rows if row["tokens"] is not None]
        lo, hi = wilson(passed, len(rows))
        payload["arms"][model] = {
            "model": model,
            "correct": passed,
            "n": len(rows),
            "accuracy": round(passed / len(rows), 4),
            "wilson95": [round(lo, 4), round(hi, 4)],
            "p50_latency_ms": round(statistics.median(latencies), 1),
            "mean_tokens": round(statistics.mean(tokens), 1) if tokens else None,
            "rows": rows,
        }
        print(f"  {passed}/{len(rows)} = {passed/len(rows):.1%} p50={payload['arms'][model]['p50_latency_ms']} ms", flush=True)

    if jev_key:
        settings = base.model_copy(
            update={
                "decision_api_key": jev_key,
                "decision_api_url": env.get("DECISION_API_URL", "https://api.typesafe.ai/v1/systemone"),
                "decision_model": env.get("DECISION_MODEL", "jev-latest"),
            }
        )
        print("== jev ==", flush=True)
        rows = await asyncio.gather(*(jev_case(settings, case, semaphore) for case in cases))
        passed = sum(1 for row in rows if row["correct"])
        latencies = [row["latency_ms"] for row in rows]
        lo, hi = wilson(passed, len(rows))
        payload["arms"]["jev"] = {
            "model": "jev-latest",
            "correct": passed,
            "n": len(rows),
            "accuracy": round(passed / len(rows), 4),
            "wilson95": [round(lo, 4), round(hi, 4)],
            "p50_latency_ms": round(statistics.median(latencies), 1),
            "mean_tokens": None,
            "rows": rows,
        }
        print(f"  {passed}/{len(rows)} = {passed/len(rows):.1%} p50={payload['arms']['jev']['p50_latency_ms']} ms", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"routing-chatanywhere-{stamp}.json"
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "routing-chatanywhere-latest.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Routing auto-test through ChatAnywhere", "", f"n={len(cases)}; temperature=0.0; generated={payload['generated_at']}", "", "| Arm | Correct | Accuracy | p50 | Mean tokens |", "|---|---:|---:|---:|---:|"]
    for arm in payload["arms"].values():
        lines.append(f"| {arm['model']} | {arm['correct']}/{arm['n']} | {arm['accuracy']:.1%} | {arm['p50_latency_ms']} ms | {arm['mean_tokens'] if arm['mean_tokens'] is not None else '-'} |")
    (OUT / "routing-chatanywhere-latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(target)


if __name__ == "__main__":
    asyncio.run(main())
