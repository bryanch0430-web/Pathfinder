"""Run existing v5 datasets with one part's model overridden.

Usage:
    PATHFINDER_SANDBOX=1 EVAL_CONCURRENCY=2 PYTHONPATH=. python scripts/v5_dataset_bench.py hongkong japan edit_vs_replan

Each arm gets its own SQLite file so plans/runs do not mix. The returned run is
written to docs/eval-artifacts/v5-model-study/.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("PATHFINDER_SANDBOX", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.deps import Container  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.core.store import Store  # noqa: E402
from app.eval import runner  # noqa: E402

OUT = Path(__file__).resolve().parent.parent.parent / "docs" / "eval-artifacts" / "v5-model-study"

ARMS = {
    "sonnet": "claude-sonnet-5.5",
    "flash": "gemini-3.5-flash-lite",
    "deepseek": "deepseek-v4-flash",
}

PURPOSE = {
    "hongkong": "plan",
    "japan": "plan",
    "travelplanner": "plan",
    "edit_vs_replan": "edit",
}


def summarize(run: dict) -> dict:
    return {
        "dataset": run["dataset_id"],
        "tag": run["experiment_tag"],
        "passed": run["passed"],
        "case_count": run["case_count"],
        "latency_ms": run["latency_ms"],
        "metrics": run.get("metrics"),
        "model_snapshot": run.get("model_snapshot"),
        "cases": [
            {
                "case_id": row["case_id"],
                "passed": row["passed"],
                "failure_class": row.get("failure_class"),
                "scores": row.get("scores"),
                "output_summary": row.get("output_summary"),
                "latency_ms": row.get("latency_ms"),
                "trace_id": row.get("trace_id"),
            }
            for row in run.get("cases", [])
        ],
    }


async def main() -> None:
    datasets = sys.argv[1:] or ["hongkong", "japan"]
    base = get_settings()
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "datasets": {}}

    wanted = {item.strip() for item in os.environ.get("BENCH_ARMS", "").split(",") if item.strip()}
    for dataset in datasets:
        purpose = PURPOSE.get(dataset, "plan")
        payload["datasets"][dataset] = {}
        for arm, model in ARMS.items():
            if wanted and arm not in wanted:
                continue
            settings = base.model_copy(update={"purpose_models": {purpose: model}})
            db = Path("data") / f"bench_{dataset}_{arm}.db"
            for suffix in ("", "-wal", "-shm"):
                try:
                    Path(str(db) + suffix).unlink()
                except FileNotFoundError:
                    pass
            store = Store(str(db))
            store.init()
            container = Container(settings=settings, store=store)
            print(f"== {dataset} / {arm} ({model}) ==", flush=True)
            run = await runner.run_dataset(container, dataset, f"v5-{dataset}-{arm}", "all")
            summary = summarize(run)
            summary["arm"] = arm
            summary["model"] = model
            summary["purpose"] = purpose
            payload["datasets"][dataset][arm] = summary
            target = OUT / f"{dataset}-{arm}-{stamp}.json"
            target.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            print(
                f"{dataset} {arm}: {summary['passed']}/{summary['case_count']} passed, "
                f"{summary['latency_ms'] / 1000:.0f}s",
                flush=True,
            )
            for metric in summary["metrics"] or []:
                print(f"  {metric['label']}: {metric['value']}{metric['unit']}", flush=True)
            await store.close()

    (OUT / f"datasets-{stamp}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (OUT / "datasets-latest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {OUT}/datasets-{stamp}.json", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
