"""Editor temperature ablation on the frozen 8-case direct-edit suite."""

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
from app.domain.models import TripContext, TripPlan  # noqa: E402
from app.evidence.models import Bundle  # noqa: E402
from app.evidence.providers import Providers  # noqa: E402
from app.plan.editor import propose_edit  # noqa: E402
from v5_editor_direct import CASES, OUT, check, load  # noqa: E402


async def main() -> None:
    base = get_settings().model_copy(update={"purpose_models": {"edit": "claude-sonnet-5.5"}})
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "model": "claude-sonnet-5.5", "arms": {}}
    for temperature in (0.0, 0.2):
        settings = base.model_copy(update={"purpose_temperatures": {"edit": temperature}})
        rows = []
        for case in CASES:
            raw, context_raw = load(case["plan"], case["prefix"])
            plan = TripPlan(**raw)
            context = TripContext(**context_raw)
            trace = TurnTrace(request_id=f"editor-t{temperature}-{case['id']}")
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
            except Exception as exc:  # noqa: BLE001
                passed, error = False, str(exc)[:240]
            finally:
                await llm.aclose()
                await providers.aclose()
            rows.append(
                {
                    "id": case["id"],
                    "passed": passed,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                    "tokens": trace.total_tokens,
                    "error": error,
                }
            )
            print(f"T={temperature:.1f} {case['id']} {'PASS' if passed else 'FAIL'} {rows[-1]['latency_ms']} ms", flush=True)
        payload["arms"][f"temperature_{temperature:.1f}"] = {
            "temperature": temperature,
            "correct": sum(1 for row in rows if row["passed"]),
            "n": len(rows),
            "p50_latency_ms": round(statistics.median(row["latency_ms"] for row in rows), 1),
            "mean_tokens": round(statistics.mean(row["tokens"] for row in rows), 1),
            "rows": rows,
        }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    (OUT / f"editor-temperature-{stamp}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "editor-temperature-latest.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Editor temperature ablation", "", "Model: `claude-sonnet-5.5`", "", "| Temperature | Correct | p50 | Mean tokens |", "|---:|---:|---:|---:|"]
    for arm in payload["arms"].values():
        lines.append(f"| {arm['temperature']:.1f} | {arm['correct']}/{arm['n']} | {arm['p50_latency_ms']} ms | {arm['mean_tokens']} |")
    (OUT / "editor-temperature-latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
