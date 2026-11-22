"""Direct editor-prompt comparison on frozen plans (no replanning)."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
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
from app.domain.models import TripContext, TripPlan  # noqa: E402
from app.evidence.models import Bundle  # noqa: E402
from app.evidence.providers import Providers  # noqa: E402
from app.plan.editor import propose_edit  # noqa: E402

OUT = Path(__file__).resolve().parent.parent.parent / "docs" / "eval-artifacts" / "v5-model-study"
ARMS = {
    "sonnet": "claude-sonnet-5.5",
    "flash": "gemini-3.5-flash-lite",
    "deepseek": "deepseek-v4-flash",
}
CASES = [
    {"id": "ed01", "plan": "data/bench_japan_sonnet.db", "prefix": "2c0b8a6e", "input": "Remove the Ghibli Museum, keep everything else.", "check": "removed", "target": "Ghibli"},
    {"id": "ed02", "plan": "data/bench_japan_sonnet.db", "prefix": "dfaa4f9d", "input": "Move Senso-ji to day 2 in the morning, keep everything else.", "check": "day_time", "target": "Senso-ji"},
    {"id": "ed03", "plan": "data/bench_japan_sonnet.db", "prefix": "2a68bc5c", "input": "Change Kiyomizu-dera to 15:00, keep the rest.", "check": "start", "target": "Kiyomizu", "start": "15:00"},
    {"id": "ed04", "plan": "data/bench_hongkong_sonnet.db", "prefix": "2fbf783a", "input": "Remove the M+ Museum, keep everything else.", "check": "removed", "target": "M+ Museum"},
]


def load(db_path: str, prefix: str) -> tuple[dict, dict]:
    conn = sqlite3.connect(db_path)
    plan = conn.execute("select body from plans where session_id like ? order by rowid desc limit 1", (prefix + "%",)).fetchone()
    context = conn.execute("select body from contexts where session_id like ? order by rowid desc limit 1", (prefix + "%",)).fetchone()
    if not plan:
        raise RuntimeError(prefix)
    return json.loads(plan[0]), json.loads(context[0]) if context else {}


def check(case: dict, plan: TripPlan) -> bool:
    items = [item for day in plan.days for item in day.items]
    if case["check"] == "removed":
        return not any(case["target"].lower() in item.name.lower() for item in items)
    if case["check"] == "start":
        return any(case["target"].lower() in item.name.lower() and item.start == case["start"] for item in items)
    if case["check"] == "day_time":
        for day_index, day in enumerate(plan.days, start=1):
            for item in day.items:
                if case["target"].lower() in item.name.lower():
                    return day_index == 2 and item.start < "12:00"
        return False
    return False


async def main() -> None:
    base = get_settings()
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "arms": {}}
    for arm, model in ARMS.items():
        settings = base.model_copy(update={"purpose_models": {"edit": model}, "purpose_temperatures": {"edit": 0.0}})
        rows = []
        for case in CASES:
            raw, context_raw = load(case["plan"], case["prefix"])
            plan = TripPlan(**raw)
            context = TripContext(**context_raw)
            trace = TurnTrace(request_id=f"editor-{arm}-{case['id']}")
            llm = LLMClient(settings, trace=trace)
            providers = Providers(settings, trace)
            started = time.perf_counter()
            try:
                outcome = await propose_edit(
                    llm=llm,
                    providers=providers,
                    settings=settings,
                    context=context,
                    plan=plan,
                    bundle=Bundle(),
                    message=case["input"],
                    scope_label=None,
                    scope_item_id=None,
                    trace=trace,
                )
                passed = check(case, outcome.plan)
                error = ""
                summary = outcome.draft.summary
            except Exception as exc:  # noqa: BLE001
                passed, error, summary = False, str(exc)[:240], ""
            finally:
                await llm.aclose()
                await providers.aclose()
            rows.append(
                {
                    "id": case["id"],
                    "input": case["input"],
                    "check": case["check"],
                    "passed": passed,
                    "summary": summary,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "tokens": trace.total_tokens,
                    "error": error,
                }
            )
            print(f"{arm} {case['id']} {'PASS' if passed else 'FAIL'} {rows[-1]['latency_ms']} ms", flush=True)
        payload["arms"][arm] = {
            "model": model,
            "correct": sum(1 for row in rows if row["passed"]),
            "n": len(rows),
            "p50_latency_ms": round(statistics.median(row["latency_ms"] for row in rows), 1),
            "mean_tokens": round(statistics.mean(row["tokens"] for row in rows), 1),
            "rows": rows,
        }
        print(f"{arm}: {payload['arms'][arm]['correct']}/{len(rows)}", flush=True)
    target = OUT / f"editor-direct-{stamp}.json"
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (OUT / "editor-direct-latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = ["# v5 editor prompt comparison", "", "| Arm | Model | Correct | p50 ms | Mean tokens |", "|---|---|---:|---:|---:|"]
    for arm, data in payload["arms"].items():
        lines.append(f"| {arm} | {data['model']} | {data['correct']}/{data['n']} | {data['p50_latency_ms']} | {data['mean_tokens']} |")
    (OUT / "editor-direct-latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(target, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
