"""Command line entry point of the evaluation harness (scenario 21): `python -m backend.eval.run`.

Runs the Appendix C metrics offline on the custom sets, selecting items with the FROZEN 70/30
split (`fixtures/splits/custom_split.json`). Items are never re-split here: if a fixture changed
since the split was frozen, `load_split` raises `SplitFrozenError` and the CLI exits with code 2
(re-freeze explicitly with `python -m backend.eval.split --refreeze`).

    python -m backend.eval.run                                  # every metric, held-out split
    python -m backend.eval.run --metric injection --out var/eval
    python -m backend.eval.run --metric routing,recovery --split dev
    python -m backend.eval.run --metric routing --metric grounding --split all

`--metric` is repeatable and also takes comma lists; `all` (the default) means every metric.
`--split` defaults to `heldout`: the proposal reports scores on held-out items and keeps dev for
prompt work ("Held-out items are not used to edit prompts"). Injection probes are all held-out,
so `--split dev` reports injection with n=0.

Which fixture set each metric consumes:
  routing     router labels (System 1 alone)
  recovery    Hong Kong disruption scenarios
  constraints Japan + Hong Kong scenarios (first planning turn)
  grounding   Japan + Hong Kong scenarios (full session, final plan)
  efficiency  Japan + Hong Kong scenarios (default vs all-four-agents ablation)
  edit_path   Japan scenarios that carry an edit
  injection   injection probes (probe turn vs paired control turn)

Output: a summary table of every report's `summary_row()` on stdout and, with `--out DIR`, one
`<metric>.json` report per metric plus `summary.json`. Exit code 0 when every metric ran (missing
a target is still 0 unless `--fail-under-target`), 1 when `--fail-under-target` and a target was
missed, 2 on a split / fixture error.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, Field

from backend.eval.datasets import (
    FIXTURES_DIR,
    FixtureError,
    load_hk_scenarios,
    load_injection_probes,
    load_japan_scenarios,
    load_router_labels,
)
from backend.eval.metrics.constraints import run_constraints
from backend.eval.metrics.edit_path import run_edit_path
from backend.eval.metrics.efficiency import run_efficiency
from backend.eval.metrics.grounding import run_grounding
from backend.eval.metrics.injection import Compromise, run_injection
from backend.eval.metrics.recovery import run_recovery
from backend.eval.metrics.routing import run_routing
from backend.eval.models import (
    CustomScenario,
    HKScenario,
    InjectionProbe,
    JapanScenario,
    RouterLabel,
    SplitSelector,
)
from backend.eval.report import MetricReport, SummaryRow
from backend.eval.split import (
    SEED_FILE,
    SPLIT_FILE,
    SplitFile,
    SplitFrozenError,
    load_split,
)
from backend.schemas.common import utcnow

METRICS: tuple[str, ...] = (
    "routing",
    "constraints",
    "recovery",
    "grounding",
    "efficiency",
    "edit_path",
    "injection",
)

_Item = TypeVar("_Item", RouterLabel, InjectionProbe, HKScenario, JapanScenario)


@dataclass(frozen=True)
class Selection:
    """The fixture items the frozen split selects for one run."""

    router: list[RouterLabel]
    probes: list[InjectionProbe]
    hk: list[HKScenario]
    japan: list[JapanScenario]

    @property
    def custom(self) -> list[CustomScenario]:
        return [*self.japan, *self.hk]


def _pick(items: Sequence[_Item], ids: set[str]) -> list[_Item]:
    """Fixture order, filtered by the frozen split's ids."""
    return [i for i in items if i.id in ids]


def select(split: SplitFile, selector: SplitSelector, fixtures_dir: Path = FIXTURES_DIR) -> Selection:
    return Selection(
        router=_pick(load_router_labels(fixtures_dir), split.ids("router", selector)),
        probes=_pick(load_injection_probes(fixtures_dir), split.ids("probes", selector)),
        hk=_pick(load_hk_scenarios(fixtures_dir), split.ids("hk", selector)),
        japan=_pick(load_japan_scenarios(fixtures_dir), split.ids("japan", selector)),
    )


def parse_metrics(values: Sequence[str] | None) -> list[str]:
    """`--metric a,b --metric c` -> [a, b, c] in canonical order; `all` (or nothing) -> every metric."""
    wanted: set[str] = set()
    for value in values or ["all"]:
        for name in (v.strip() for v in value.split(",")):
            if not name:
                continue
            if name == "all":
                wanted |= set(METRICS)
            elif name in METRICS:
                wanted.add(name)
            else:
                raise ValueError(f"unknown metric {name!r}; choose from {', '.join(METRICS)} or all")
    return [m for m in METRICS if m in wanted]


@dataclass(frozen=True)
class RunOptions:
    compromise: Compromise = Compromise.BOTH
    travelplanner: Path | None = None


def _driver(metric: str, sel: Selection, split_name: str, opts: RunOptions) -> Awaitable[MetricReport]:
    drivers: dict[str, Callable[[], Awaitable[MetricReport]]] = {
        "routing": lambda: run_routing(sel.router, split_name=split_name),
        "constraints": lambda: run_constraints(
            sel.custom, split_name=split_name, travelplanner_path=opts.travelplanner
        ),
        "recovery": lambda: run_recovery(sel.hk, split_name=split_name),
        "grounding": lambda: run_grounding(sel.custom, split_name=split_name),
        "efficiency": lambda: run_efficiency(sel.custom, split_name=split_name),
        "edit_path": lambda: run_edit_path(sel.japan, split_name=split_name),
        "injection": lambda: run_injection(sel.probes, split_name=split_name, compromise=opts.compromise),
    }
    return drivers[metric]()


class SummaryEntry(SummaryRow):
    seconds: float


class RunSummary(BaseModel):
    split: str
    seed: int
    split_created_at: str
    fixture_sha256: dict[str, str]
    metrics: list[str]
    rows: list[SummaryEntry] = Field(default_factory=list)
    total_seconds: float = 0.0


async def run_metrics(
    metrics: Sequence[str], sel: Selection, split_name: str, opts: RunOptions
) -> list[tuple[MetricReport, float]]:
    out: list[tuple[MetricReport, float]] = []
    for metric in metrics:
        t0 = time.perf_counter()
        report = await _driver(metric, sel, split_name, opts)
        out.append((report, time.perf_counter() - t0))
    return out


def format_table(rows: Sequence[SummaryEntry]) -> str:
    def met(value: bool | None) -> str:
        return "n/a" if value is None else ("yes" if value else "NO")

    header = ("metric", "split", "n", "met", "secs", "target", "headline")
    body = [(r.metric, r.split, str(r.n), met(r.met), f"{r.seconds:.1f}", r.target, r.headline) for r in rows]
    widths = [max(len(row[i]) for row in [header, *body]) for i in range(len(header) - 1)]
    lines = []
    for row in [header, *body]:
        cells = [cell.ljust(widths[i]) for i, cell in enumerate(row[:-1])]
        lines.append("  ".join([*cells, row[-1]]))
    lines.insert(1, "  ".join("-" * w for w in widths) + "  " + "-" * len("headline"))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.eval.run",
        description="Run the Appendix C evaluation metrics offline on the frozen split.",
    )
    parser.add_argument(
        "--metric",
        action="append",
        metavar="NAME[,NAME...]",
        help=f"metric(s) to run, repeatable or comma separated: {', '.join(METRICS)}, all (default: all)",
    )
    parser.add_argument(
        "--split",
        choices=[s.value for s in SplitSelector],
        default=SplitSelector.HELDOUT.value,
        help="frozen partition to score (default: heldout; dev is for prompt work)",
    )
    parser.add_argument("--out", type=Path, default=None, help="directory for <metric>.json and summary.json")
    parser.add_argument("--fixtures-dir", type=Path, default=FIXTURES_DIR)
    parser.add_argument("--split-file", type=Path, default=SPLIT_FILE)
    parser.add_argument("--seed-file", type=Path, default=SEED_FILE)
    parser.add_argument(
        "--compromise",
        choices=[c.value for c in Compromise],
        default=Compromise.BOTH.value,
        help="injection: which model runs as the injection-compliant mock (default: both)",
    )
    parser.add_argument(
        "--travelplanner",
        type=Path,
        default=None,
        help="constraints: path to a downloaded TravelPlanner split (loaded and counted, not scored)",
    )
    parser.add_argument(
        "--fail-under-target", action="store_true", help="exit 1 when any metric misses its target"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        metrics = parse_metrics(args.metric)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    selector = SplitSelector(args.split)
    try:
        split = load_split(args.fixtures_dir, args.split_file, seed_path=args.seed_file)
        sel = select(split, selector, args.fixtures_dir)
    except (SplitFrozenError, FileNotFoundError, FixtureError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    opts = RunOptions(compromise=Compromise(args.compromise), travelplanner=args.travelplanner)
    t0 = time.perf_counter()
    results = asyncio.run(run_metrics(metrics, sel, selector.value, opts))
    summary = RunSummary(
        split=selector.value,
        seed=split.seed,
        split_created_at=split.created_at.isoformat(),
        fixture_sha256=split.fixture_sha256,
        metrics=metrics,
        rows=[
            SummaryEntry(**report.summary_row().model_dump(), seconds=round(secs, 3)) for report, secs in results
        ],
        total_seconds=round(time.perf_counter() - t0, 3),
    )
    if args.out is not None:
        args.out.mkdir(parents=True, exist_ok=True)
        for report, _ in results:
            (args.out / f"{report.metric}.json").write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        payload = {**json.loads(summary.model_dump_json()), "written_at": utcnow().isoformat()}
        (args.out / "summary.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(format_table(summary.rows))
    for report, _ in results:
        for note in report.notes:
            print(f"note [{report.metric}]: {note}")
    print(f"split={selector.value} seed={split.seed} total {summary.total_seconds:.1f}s")
    if args.out is not None:
        print(f"wrote {len(results)} report(s) and summary.json to {args.out}")
    if args.fail_under_target and any(r.met is False for r in summary.rows):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
