"""Memory extraction comparison on 8 synthetic traveller histories."""

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

from app.application.memory import derive_memory  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.core.llm import LLMClient  # noqa: E402
from app.domain.models import (  # noqa: E402
    ChatMessage, DateRange, DayPlan, Destination, PlanItem, TripContext, TripPlan,
)

OUT = Path(__file__).resolve().parent.parent.parent / "docs" / "eval-artifacts" / "v5-model-study"
FIXTURES = Path("eval/fixtures/v5_study")
ARMS = {
    "sonnet": "claude-sonnet-5.5",
    "flash": "gemini-3.5-flash-lite",
    "deepseek": "deepseek-v4-flash",
}


def build(case: dict):
    plan = TripPlan(
        trip_id=f"mem-{case['id']}", session_id=f"mem-{case['id']}",
        destination=Destination(name=case["destination"]),
        dates=DateRange(start="2026-12-01", end="2026-12-03"),
        summary=case["summary"],
        days=[
            DayPlan(
                date="2026-12-01",
                items=[
                    PlanItem(id=f"i{index}", name=name, tags=case.get("tags", []))
                    for index, name in enumerate(case.get("stops", []))
                ],
            )
        ],
    )
    context = TripContext(session_id=f"mem-{case['id']}", destination=case["destination"], days=3)
    messages = [
        ChatMessage(id=f"m{index}", role="user", content=text)
        for index, text in enumerate(case.get("messages", []))
    ]
    return plan, context, messages


def hit(gold: str, preferences: list[str]) -> bool:
    text = " ".join(preferences).lower()
    alternatives = [part.strip().lower() for part in gold.split("/") if part.strip()]
    if not alternatives:
        return True
    return any(all(word in text for word in alt.replace("-", " ").split()) for alt in alternatives)


async def main() -> None:
    base = get_settings()
    cases = json.loads((FIXTURES / "memory_cases.json").read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "n": len(cases), "arms": {}}
    for arm, model in ARMS.items():
        settings = base.model_copy(
            update={"purpose_models": {"memory": model}, "purpose_temperatures": {"memory": 0.0}}
        )
        rows = []
        for case in cases:
            plan, context, messages = build(case)
            llm = LLMClient(settings)
            started = time.perf_counter()
            try:
                draft = await derive_memory(llm=llm, plan=plan, context=context, messages=messages)
                preferences = list(draft.preferences) if draft else []
                error = ""
            except Exception as exc:  # noqa: BLE001
                preferences, error = [], str(exc)[:240]
            finally:
                await llm.aclose()
            hits = [gold for gold in case["gold"] if hit(gold, preferences)]
            rows.append(
                {
                    "id": case["id"],
                    "preferences": preferences,
                    "gold": case["gold"],
                    "hits": hits,
                    "recall": round(len(hits) / len(case["gold"]), 3) if case["gold"] else 1.0,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "error": error,
                }
            )
            print(f"{arm} {case['id']} recall {rows[-1]['recall']:.0%}", flush=True)
        with_gold = [row for row in rows if row["gold"]]
        payload["arms"][arm] = {
            "model": model,
            "mean_recall": round(statistics.mean(row["recall"] for row in with_gold), 3) if with_gold else 0.0,
            "p50_latency_ms": round(statistics.median(row["latency_ms"] for row in rows), 1),
            "rows": rows,
        }
        print(f"{arm}: recall {payload['arms'][arm]['mean_recall']:.1%}", flush=True)
    target = OUT / f"memory-{stamp}.json"
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (OUT / "memory-latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = ["# v5 memory extraction comparison", "", "| Arm | Model | Mean recall | p50 ms |", "|---|---|---:|---:|"]
    for arm, data in payload["arms"].items():
        lines.append(f"| {arm} | {data['model']} | {data['mean_recall']:.1%} | {data['p50_latency_ms']} |")
    (OUT / "memory-latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(target, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
