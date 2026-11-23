"""Auto-test the 8 editor cases through ChatAnywhere at 0.0 and 0.2."""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.config import get_settings  # noqa: E402
from app.core.llm import LLMClient  # noqa: E402
from app.core.trace import TurnTrace  # noqa: E402
from app.domain.models import TripContext, TripPlan  # noqa: E402
from app.evidence.models import Bundle  # noqa: E402
from app.evidence.providers import Providers  # noqa: E402
from app.plan.editor import propose_edit  # noqa: E402
from v5_editor_direct import CASES, check, load  # noqa: E402

OUT = PROJECT / "docs" / "eval-artifacts" / "autotest"
ENV = PROJECT / ".env.autotest"


def env_values():
    values = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    return values


async def main():
    env = env_values()
    base_url = env.get("AUTOTEST_BASE_URL", "https://api.chatanywhere.org/v1")
    key = env.get("AUTOTEST_API_KEY", "")
    model = env.get("AUTOTEST_EDITOR_MODEL", "claude-sonnet-5-5")
    base = get_settings().model_copy(update={"llm_provider": "openai", "openai_base_url": base_url, "openai_api_key": key, "openai_model": model, "llm_model": model})
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "suite": "editor", "n": len(CASES), "arms": {}}
    semaphore = asyncio.Semaphore(int(os.environ.get("AUTOTEST_CONCURRENCY", "1")))

    async def one(temperature, case):
        async with semaphore:
            settings = base.model_copy(update={"purpose_models": {"edit": model}, "purpose_temperatures": {"edit": temperature}})
            raw, context_raw = load(case["plan"], case["prefix"])
            plan = TripPlan(**raw)
            context = TripContext(**context_raw)
            trace = TurnTrace(request_id=f"editor-{temperature}-{case['id']}")
            llm = LLMClient(settings, trace=trace)
            providers = Providers(settings, trace)
            started = time.perf_counter()
            try:
                outcome = await propose_edit(
                    llm=llm, providers=providers, settings=settings, context=context, plan=plan,
                    bundle=Bundle(), message=case["input"], scope_label=None, scope_item_id=None, trace=trace,
                )
                passed = check(case, outcome.plan)
                error = ""
            except Exception as exc:  # noqa: BLE001
                passed, error = False, str(exc)[:300]
            finally:
                await llm.aclose()
                await providers.aclose()
            return {
                "id": case["id"], "input": case["input"], "passed": passed,
                "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "tokens": trace.total_tokens, "error": error,
            }

    for temperature in (0.0, 0.2):
        rows = await asyncio.gather(*(one(temperature, case) for case in CASES))
        passed = sum(1 for row in rows if row["passed"])
        payload["arms"][f"{model} @ {temperature:.1f}"] = {
            "model": model, "temperature": temperature, "correct": passed, "n": len(rows),
            "p50_latency_ms": round(statistics.median(row["latency_ms"] for row in rows), 1),
            "mean_tokens": round(statistics.mean(row["tokens"] for row in rows), 1),
            "rows": rows,
        }
        print(f"{model} @ {temperature:.1f}: {passed}/{len(rows)}", flush=True)

    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    (OUT / f"editor-chatanywhere-{stamp}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "editor-chatanywhere-latest.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = ["# Editor auto-test", "", "| Arm | Correct | p50 | Mean tokens |", "|---|---:|---:|---:|"]
    for arm in payload["arms"].values():
        lines.append(f"| {arm['model']} @ {arm['temperature']} | {arm['correct']}/{arm['n']} | {arm['p50_latency_ms']} ms | {arm['mean_tokens']} |")
    (OUT / "editor-chatanywhere-latest.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("written")


if __name__ == "__main__":
    asyncio.run(main())
