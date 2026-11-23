"""Run the remaining case-list suites through the real v5 pipeline on ChatAnywhere."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PATHFINDER_SANDBOX", "1")
PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.deps import Container  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.core.store import Store  # noqa: E402
from app.eval import runner  # noqa: E402

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
    if not key:
        raise SystemExit("AUTOTEST_API_KEY missing")
    model = env.get("AUTOTEST_WRITER_MODEL", "claude-sonnet-5-5")
    verifier = env.get("AUTOTEST_VERIFIER_MODEL", "deepseek-v4.1-flash")
    settings = get_settings().model_copy(
        update={
            "llm_provider": "openai",
            "openai_base_url": base_url,
            "openai_api_key": key,
            "openai_model": model,
            "llm_model": model,
            "purpose_models": {
                "understand": model,
                "plan": model,
                "edit": model,
                "ask": model,
                "memory": model,
                "verify": verifier,
            },
            "purpose_temperatures": {"verify": 0.0},
            "verifier_base_url": base_url,
            "verifier_api_key": key,
            "verifier_model": verifier,
            "llm_temperature": 0.2,
            "sandbox": True,
        }
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "base_url": base_url, "datasets": {}}

    for dataset in ("hongkong", "japan", "travelplanner", "efficiency", "edit_vs_replan", "injection"):
        db = PROJECT / "backend" / "data" / f"autotest_{dataset}.db"
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(str(db) + suffix).unlink()
            except FileNotFoundError:
                pass
        store = Store(str(db))
        store.init()
        container = Container(settings=settings, store=store)
        print(f"== {dataset} ==", flush=True)
        try:
            run = await runner.run_dataset(container, dataset, f"autotest-{dataset}", "all")
            summary = {
                "dataset": dataset,
                "passed": run["passed"],
                "case_count": run["case_count"],
                "latency_ms": run["latency_ms"],
                "metrics": run.get("metrics"),
                "cases": [
                    {
                        "case_id": row["case_id"],
                        "passed": row["passed"],
                        "failure_class": row.get("failure_class", ""),
                        "scores": row.get("scores"),
                        "output_summary": row.get("output_summary", "")[:600],
                        "latency_ms": row.get("latency_ms"),
                        "trace_id": row.get("trace_id", ""),
                    }
                    for row in run.get("cases", [])
                ],
            }
            payload["datasets"][dataset] = summary
            (OUT / f"{dataset}-chatanywhere-{stamp}.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
            (OUT / f"{dataset}-chatanywhere-latest.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  {summary['passed']}/{summary['case_count']} passed, {summary['latency_ms']/1000:.0f}s", flush=True)
        finally:
            await store.close()

    (OUT / f"remaining-chatanywhere-{stamp}.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "remaining-chatanywhere-latest.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print("done", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
