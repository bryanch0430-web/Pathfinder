"""Command-line evaluation runner.

Usage:
    python -m app.eval_run --dataset hongkong --tag baseline-v1
    python -m app.eval_run --dataset injection --split heldout --out data/eval
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from app.container import build_container
from app.evaluation import export_markdown


async def _run(args: argparse.Namespace) -> int:
    container = build_container()
    datasets = {dataset.id for dataset in container.store.list_eval_datasets()}
    if args.dataset not in datasets:
        print(f"Unknown dataset {args.dataset!r}. Available: {', '.join(sorted(datasets))}", file=sys.stderr)
        return 2
    try:
        run = await container.evaluation.run(
            dataset_id=args.dataset,
            experiment_tag=args.tag,
            split=args.split,
            include_heldout=args.split == "all",
        )
    except PermissionError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    print(f"\nRun {run.id} · {run.experiment_tag} · {run.dataset_id}")
    print(f"{'metric':28} {'value':>10} {'unit':>6} {'baseline':>10} {'delta':>8}")
    for metric in run.metrics:
        print(
            f"{metric.label:28} {metric.value:>10} {metric.unit:>6} "
            f"{metric.baseline if metric.baseline is not None else '-':>10} "
            f"{metric.delta if metric.delta is not None else '-':>8}"
        )
    passed = sum(1 for case in run.cases if case.passed)
    print(f"cases: {passed}/{len(run.cases)} passed · latency {run.latency_ms} ms")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = out_dir / f"{run.id}.md"
    report.write_text(export_markdown(run), encoding="utf-8")
    print(f"report: {report}")
    return 0 if passed == len(run.cases) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Pathfinder evaluation dataset.")
    parser.add_argument("--dataset", default="hongkong", help="routing | injection | hongkong | japan | travelplanner")
    parser.add_argument("--tag", default="baseline-v1", help="experiment label")
    parser.add_argument("--split", default="all", choices=["all", "dev", "heldout"])
    parser.add_argument("--out", default="data/eval", help="report directory")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
