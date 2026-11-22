"""v5 part-model study: analyst/router comparison on the frozen routing set.

One run per arm; the same 84 labelled utterances; missing/error counts as wrong.
Outputs JSON + a Markdown summary under docs/eval-artifacts/v5-model-study/.

Run:
    PATHFINDER_SANDBOX=1 PYTHONPATH=. python scripts/v5_part_bench.py routing
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

os.environ.setdefault("PATHFINDER_SANDBOX", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.core.llm import LLMClient  # noqa: E402
from app.domain.models import Destination, TripContext, TripPlan  # noqa: E402
from app.eval import datasets  # noqa: E402
from app.core.trace import TurnTrace  # noqa: E402
from app.understand.jev import jev_route  # noqa: E402
from app.understand.pipeline import understand  # noqa: E402

OUT = Path(__file__).resolve().parent.parent.parent / "docs" / "eval-artifacts" / "v5-model-study"

ARMS = [
    {"key": "sonnet-0.0", "model": "claude-sonnet-5.5", "temperature": 0.0},
    {"key": "sonnet-0.2", "model": "claude-sonnet-5.5", "temperature": 0.2},
    {"key": "flash-lite-0.0", "model": "gemini-3.5-flash-lite", "temperature": 0.0},
    {"key": "deepseek-0.0", "model": "deepseek-v4-flash", "temperature": 0.0},
]


def wilson(success: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    phat = success / total
    denom = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denom
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * total)) / total) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def mcnemar(a: list[bool], b: list[bool]) -> tuple[int, int, float]:
    """Exact two-sided McNemar on paired booleans."""
    b01 = sum(1 for x, y in zip(a, b) if x and not y)
    b10 = sum(1 for x, y in zip(a, b) if y and not x)
    n = b01 + b10
    if n == 0:
        return b01, b10, 1.0
    k = min(b01, b10)
    tail = sum(math.comb(n, i) for i in range(0, k + 1)) / (2**n)
    return b01, b10, min(1.0, 2 * tail)


def summarize(cases: list[dict], arm: dict) -> dict:
    total = len(cases)
    passed = sum(1 for row in cases if row["correct"])
    lo, hi = wilson(passed, total)
    by_intent: dict[str, dict] = {}
    for row in cases:
        bucket = by_intent.setdefault(row["expected"], {"n": 0, "correct": 0})
        bucket["n"] += 1
        bucket["correct"] += int(row["correct"])
    latencies = [row["latency_ms"] for row in cases if row.get("latency_ms") is not None]
    tokens = [row["tokens"] for row in cases if row.get("tokens") is not None]
    confidences = [row["confidence"] for row in cases if row.get("confidence") is not None]
    gates = []
    for cutoff in (0.6, 0.7, 0.8, 0.9):
        kept = [row for row in cases if row.get("confidence") is not None and row["confidence"] >= cutoff]
        gates.append(
            {
                "cutoff": cutoff,
                "coverage": round(len(kept) / total, 3) if total else 0.0,
                "accuracy_on_kept": round(sum(1 for row in kept if row["correct"]) / len(kept), 3) if kept else None,
                "escalated": total - len(kept),
            }
        )
    return {
        "arm": arm,
        "n": total,
        "correct": passed,
        "accuracy": round(passed / total, 4) if total else 0.0,
        "wilson95": [round(lo, 4), round(hi, 4)],
        "by_intent": {
            intent: {
                "n": bucket["n"],
                "correct": bucket["correct"],
                "accuracy": round(bucket["correct"] / bucket["n"], 4) if bucket["n"] else 0.0,
            }
            for intent, bucket in sorted(by_intent.items())
        },
        "p50_latency_ms": round(statistics.median(latencies), 1) if latencies else None,
        "p95_latency_ms": round(sorted(latencies)[int(len(latencies) * 0.95) - 1], 1) if len(latencies) >= 2 else None,
        "mean_tokens": round(sum(tokens) / len(tokens), 1) if tokens else None,
        "mean_confidence": round(sum(confidences) / len(confidences), 3) if confidences else None,
        "gates": gates,
    }


async def run_llm_arm(settings, cases, arm: dict, semaphore: asyncio.Semaphore) -> list[dict]:
    async def one(case) -> dict:
        async with semaphore:
            trace = TurnTrace(request_id=f"bench-{arm['key']}-{case.id}")
            llm = LLMClient(settings, trace=trace)
            started = time.perf_counter()
            try:
                understanding = await understand(
                    llm=llm,
                    settings=settings,
                    context=TripContext(
                        session_id=f"bench-{case.id}",
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
                predicted = understanding.parsed.intent
                confidence = understanding.parsed.confidence
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

    settings = settings.model_copy(
        update={
            "purpose_models": {"understand": arm["model"]},
            "purpose_temperatures": {"understand": arm["temperature"]},
        }
    )
    return list(await asyncio.gather(*(one(case) for case in cases)))


async def run_jev(cases, semaphore: asyncio.Semaphore) -> list[dict]:
    settings = get_settings()

    async def one(case) -> dict:
        async with semaphore:
            started = time.perf_counter()
            try:
                decision = await jev_route(
                    settings=settings,
                    context=TripContext(
                        session_id=f"bench-jev-{case.id}",
                        destination=case.destination,
                        origin=case.origin,
                        currency=case.currency or settings.default_currency,
                        language="en",
                    ),
                    plan=TripPlan(
                        trip_id=f"routing-{case.id}",
                        session_id=f"routing-{case.id}",
                        destination=Destination(name=case.destination or "an existing trip"),
                        summary="An existing plan is on file.",
                    ),
                    message=case.input,
                    history=[],
                )
                predicted = decision.intent if decision else "error"
                confidence = decision.confidence if decision else None
                error = "" if decision else "no decision"
            except Exception as exc:  # noqa: BLE001
                predicted, confidence, error = "error", None, str(exc)[:240]
            return {
                "id": case.id,
                "input": case.input,
                "expected": case.expected_intent,
                "predicted": predicted,
                "correct": predicted == case.expected_intent,
                "confidence": round(float(confidence), 3) if confidence is not None else None,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "tokens": None,
                "error": error,
            }

    return list(await asyncio.gather(*(one(case) for case in cases)))


async def main() -> None:
    concurrency = int(os.environ.get("BENCH_CONCURRENCY", "4"))
    semaphore = asyncio.Semaphore(concurrency)
    cases = datasets.load("routing")
    settings = get_settings()
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    arms: dict[str, list[dict]] = {}

    print(f"routing: {len(cases)} cases, concurrency {concurrency}", flush=True)
    wanted = {item.strip() for item in os.environ.get("BENCH_ARMS", "").split(",") if item.strip()}
    for arm in ARMS:
        if wanted and arm["key"] not in wanted:
            continue
        print(f"  arm {arm['key']} …", flush=True)
        arms[arm["key"]] = await run_llm_arm(settings, cases, arm, semaphore)
        summary = summarize(arms[arm["key"]], arm)
        print(
            f"    {summary['correct']}/{summary['n']} = {summary['accuracy']:.1%} "
            f"({summary['wilson95'][0]:.1%}–{summary['wilson95'][1]:.1%}), "
            f"p50 {summary['p50_latency_ms']} ms",
            flush=True,
        )

    print("  arm jev …", flush=True)
    jev_cases = await run_jev(cases, semaphore)
    jev_arm = {"key": "jev", "model": settings.decision_model, "temperature": None}
    arms["jev"] = jev_cases
    summary = summarize(jev_cases, jev_arm)
    print(
        f"    {summary['correct']}/{summary['n']} = {summary['accuracy']:.1%} "
        f"({summary['wilson95'][0]:.1%}–{summary['wilson95'][1]:.1%}), "
        f"p50 {summary['p50_latency_ms']} ms",
        flush=True,
    )

    baseline_key = ARMS[0]["key"]
    baseline = arms.get(baseline_key, [])
    summaries = {}
    for key, rows in arms.items():
        arm = next((item for item in ARMS if item["key"] == key), jev_arm)
        summaries[key] = summarize(rows, arm)
        summaries[key]["vs_baseline"] = None
    for key in summaries:
        if key == baseline_key or not baseline:
            continue
        correct_a = [row["correct"] for row in baseline]
        correct_b = [row["correct"] for row in arms[key]]
        b01, b10, p = mcnemar(correct_a, correct_b)
        summaries[key]["vs_baseline"] = {
            "baseline": baseline_key,
            "only_baseline": b01,
            "only_arm": b10,
            "mcnemar_p": round(p, 4),
        }

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dataset": "routing",
        "dataset_version": "2026-11-05",
        "n": len(cases),
        "protocol": "one run per arm; missing/error counts as wrong; same frozen utterances; Wilson 95%; exact McNemar vs sonnet-0.0",
        "summaries": summaries,
        "cases": arms,
    }
    target = OUT / f"routing-{stamp}.json"
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {target}", flush=True)

    lines = [
        "# v5 analyst/router comparison",
        "",
        f"Generated: {payload['generated_at']}",
        f"Dataset: routing, n={len(cases)}, {payload['protocol']}",
        "",
        "| Arm | Model | Temp | Correct | Accuracy | Wilson 95% | p50 ms | Mean tokens |",
        "|---|---|---:|---:|---:|---|---:|---:|",
    ]
    for key, summary in summaries.items():
        arm = summary["arm"]
        temp = "—" if arm.get("temperature") is None else arm["temperature"]
        lines.append(
            f"| {key} | {arm.get('model','')} | {temp} | {summary['correct']}/{summary['n']} | "
            f"{summary['accuracy']:.1%} | {summary['wilson95'][0]:.1%}–{summary['wilson95'][1]:.1%} | "
            f"{summary['p50_latency_ms']} | {summary['mean_tokens']} |"
        )
    lines += ["", "## Accuracy by intent", "", "| Arm | plan | modify | ask | unclear |", "|---|---:|---:|---:|---:|"]
    for key, summary in summaries.items():
        cells = []
        for intent in ("plan", "modify", "ask", "unclear"):
            bucket = summary["by_intent"].get(intent)
            cells.append(f"{bucket['accuracy']:.1%} ({bucket['correct']}/{bucket['n']})" if bucket else "—")
        lines.append(f"| {key} | " + " | ".join(cells) + " |")
    lines += ["", "## Confidence gate", "", "| Arm | cutoff | coverage | accuracy on kept | escalated |", "|---|---:|---:|---:|---:|"]
    for key, summary in summaries.items():
        for gate in summary["gates"]:
            accuracy = "—" if gate["accuracy_on_kept"] is None else f"{gate['accuracy_on_kept']:.1%}"
            lines.append(f"| {key} | {gate['cutoff']} | {gate['coverage']:.0%} | {accuracy} | {gate['escalated']} |")
    lines += ["", "## Paired comparison vs sonnet-0.0", "", "| Arm | only baseline | only arm | McNemar p |", "|---|---:|---:|---:|"]
    for key, summary in summaries.items():
        if summary["vs_baseline"]:
            comp = summary["vs_baseline"]
            lines.append(f"| {key} | {comp['only_baseline']} | {comp['only_arm']} | {comp['mcnemar_p']} |")
    (OUT / f"routing-{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "routing-latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (OUT / "routing-latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
