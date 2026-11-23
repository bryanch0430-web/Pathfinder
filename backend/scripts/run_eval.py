"""Run one or more evaluation datasets through the real pipeline (sandbox evidence).

Usage:
    PYTHONPATH=. python scripts/run_eval.py travelplanner routing
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("PATHFINDER_SANDBOX", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api.deps import Container  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.core.store import Store  # noqa: E402
from app.eval import runner  # noqa: E402


async def main() -> None:
    dataset_ids = sys.argv[1:] or ["travelplanner"]
    settings = get_settings()
    store = Store(settings.database_file)
    store.init()
    container = Container(settings=settings, store=store)
    for dataset_id in dataset_ids:
        print(f"running {dataset_id} …", flush=True)
        run = await runner.run_dataset(container, dataset_id, os.environ.get("EVAL_TAG", "v5"), "all")
        print(
            f"\n{dataset_id}: {run['passed']}/{run['case_count']} passed in {run['latency_ms'] / 1000:.0f}s",
            flush=True,
        )
        for metric in run["metrics"]:
            print(
                f"  {metric['label']}: {metric['value']}{metric['unit']}"
                + (f"  (v4 {metric['baseline']}, delta {metric['delta']:+})" if metric.get("baseline") is not None else ""),
                flush=True,
            )
        failures = [row for row in run["cases"] if not row["passed"]]
        for row in failures[:12]:
            print(f"  FAIL {row['case_id']} [{row['failure_class']}] {row['input'][:80]}", flush=True)
            print(f"       {row['output_summary'][:180]}", flush=True)
        print(flush=True)


if __name__ == "__main__":
    asyncio.run(main())
