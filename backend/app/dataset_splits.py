"""Frozen, stratified dev/held-out split for the custom evaluation sets.

Proposal TC-06 (p4, l.132-135): the custom sets are split 70/30 into development
and held-out, stratified by city and constraint type, and the split is frozen
before the first scored run.

The split still lives on each fixture row (``"split": "dev" | "heldout"``). This
module computes that assignment deterministically and records a manifest,
``eval/splits/split-<seed>.json``, holding the seed, the counts per stratum and a
line-ending-independent hash of every frozen fixture. A fixture edited after the
freeze no longer matches its hash, which the tests and the trial report expose.

Assignment: the held-out total is round(N * 0.3); each stratum gets its
proportional share, and the leftover places go to the strata with the largest
remainder (Hamilton allocation). Inside a stratum the held-out items are the
first ones in seeded-hash order, so adding items moves as few as possible.

Usage (from backend/):
    python -m app.dataset_splits freeze routing injection
    python -m app.dataset_splits check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.domain.models import EvalMetric

ROOT = Path(__file__).resolve().parents[1] / "eval"
FIXTURES = ROOT / "fixtures"
SPLITS = ROOT / "splits"
SEED = 20261009
DEV_SHARE = 0.7
MANIFEST = SPLITS / f"split-{SEED}.json"

FIXTURE_FILES = {
    "routing": "routing.jsonl",
    "injection": "injection.jsonl",
    "hongkong": "hongkong.json",
    "japan": "japan.json",
}

#: what "city and constraint type" means for each kind of set
STRATA = {
    "routing": "expected_intent x destination",
    "injection": "attack x channel",
    "planning": "destination x constraint type (fault, else hard constraints, else baseline)",
}


def fixture_path(dataset: str) -> Path:
    return FIXTURES / FIXTURE_FILES[dataset]


def load_rows(dataset: str) -> list[dict[str, Any]]:
    path = fixture_path(dataset)
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)


def content_hash(rows: list[dict[str, Any]]) -> str:
    """Hash of the parsed rows, so CRLF checkouts and whitespace do not matter."""
    canonical = "\n".join(json.dumps(row, sort_keys=True, ensure_ascii=False) for row in rows)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def stratum(dataset: str, row: dict[str, Any]) -> str:
    if dataset == "routing":
        return f"{row.get('expected_intent') or 'none'}|{row.get('destination') or 'any'}"
    if dataset == "injection":
        return f"{row.get('attack') or 'none'}|{row.get('channel') or 'user'}"
    if row.get("fault"):
        constraint = str(row["fault"])
    elif row.get("constraints"):
        constraint = "hard_constraints"
    else:
        constraint = "baseline"
    return f"{row.get('destination') or 'any'}|{constraint}"


def _order(dataset: str, item_id: str, seed: int) -> str:
    return hashlib.sha256(f"{seed}:{dataset}:{item_id}".encode("utf-8")).hexdigest()


def assign(dataset: str, rows: list[dict[str, Any]], *, seed: int = SEED, dev_share: float = DEV_SHARE) -> dict[str, str]:
    """Deterministic stratified assignment: {row id: "dev" | "heldout"}."""
    groups: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        groups[stratum(dataset, row)].append(str(row["id"]))
    held_share = 1.0 - dev_share
    target = math.floor(len(rows) * held_share + 0.5)
    exact = {name: len(ids) * held_share for name, ids in groups.items()}
    quota = {name: math.floor(value) for name, value in exact.items()}
    leftover = target - sum(quota.values())
    by_remainder = sorted(
        groups,
        key=lambda name: (-(exact[name] - quota[name]), _order(dataset, name, seed)),
    )
    for name in by_remainder[: max(leftover, 0)]:
        quota[name] += 1
    out: dict[str, str] = {}
    for name, ids in groups.items():
        ranked = sorted(ids, key=lambda item_id: _order(dataset, item_id, seed))
        held = set(ranked[: quota[name]])
        for item_id in ids:
            out[item_id] = "heldout" if item_id in held else "dev"
    return out


def summarise(dataset: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    strata: dict[str, dict[str, int]] = defaultdict(lambda: {"dev": 0, "heldout": 0})
    for row in rows:
        strata[stratum(dataset, row)][row.get("split", "dev")] += 1
    dev = sum(1 for row in rows if row.get("split", "dev") == "dev")
    return {
        "fixture": FIXTURE_FILES[dataset],
        "sha256": content_hash(rows),
        "items": len(rows),
        "dev": dev,
        "heldout": len(rows) - dev,
        "stratified_by": STRATA.get(dataset, STRATA["planning"]),
        "strata": {name: strata[name] for name in sorted(strata)},
    }


def check(dataset: str, rows: list[dict[str, Any]] | None = None, *, manifest: dict | None = None) -> list[str]:
    """Problems with a dataset's split: unfrozen, edited after the freeze, or not the stratified 70/30."""
    rows = load_rows(dataset) if rows is None else rows
    manifest = read_manifest() if manifest is None else manifest
    entry = (manifest.get("datasets") or {}).get(dataset)
    if entry is None:
        return [f"{dataset}: not in {MANIFEST.name}; run `python -m app.dataset_splits freeze {dataset}`"]
    problems: list[str] = []
    if entry.get("sha256") != content_hash(rows):
        problems.append(f"{dataset}: fixture changed after the split was frozen")
    expected = assign(dataset, rows, seed=int(manifest.get("seed", SEED)), dev_share=float(manifest.get("dev_share", DEV_SHARE)))
    moved = sorted(row["id"] for row in rows if row.get("split", "dev") != expected[str(row["id"])])
    if moved:
        problems.append(f"{dataset}: split differs from the stratified assignment for {', '.join(moved[:8])}")
    return problems


def is_frozen(dataset: str) -> bool:
    try:
        return not check(dataset)
    except (OSError, ValueError, KeyError):
        return False


def heldout_share(dataset: str) -> float:
    rows = load_rows(dataset)
    if not rows:
        return 0.0
    return round(100.0 * sum(1 for row in rows if row.get("split") == "heldout") / len(rows), 1)


def split_metrics(dataset: str) -> list[EvalMetric]:
    """Trial-report metrics for TC-06: the held-out share and whether the split is still frozen."""
    return [
        EvalMetric(key="heldout_share", label="Held-out share", value=heldout_share(dataset)),
        EvalMetric(key="split_frozen", label="Split frozen", value=100.0 if is_frozen(dataset) else 0.0),
    ]


def read_manifest() -> dict[str, Any]:
    if not MANIFEST.exists():
        return {"seed": SEED, "dev_share": DEV_SHARE, "datasets": {}}
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _write_fixture(dataset: str, rows: list[dict[str, Any]]) -> None:
    path = fixture_path(dataset)
    if path.suffix == ".jsonl":
        text = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
    else:
        text = json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def freeze(datasets: list[str]) -> dict[str, Any]:
    """Write the stratified split onto each fixture and record it in the manifest."""
    manifest = read_manifest()
    manifest.update({"seed": SEED, "dev_share": DEV_SHARE})
    entries = manifest.setdefault("datasets", {})
    for dataset in datasets:
        rows = load_rows(dataset)
        assignment = assign(dataset, rows)
        for row in rows:
            row["split"] = assignment[str(row["id"])]
        _write_fixture(dataset, rows)
        entries[dataset] = summarise(dataset, rows)
    manifest["datasets"] = {name: entries[name] for name in sorted(entries)}
    SPLITS.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.dataset_splits", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    freeze_cmd = sub.add_parser("freeze", help="assign the stratified split and write the manifest")
    freeze_cmd.add_argument("datasets", nargs="+", choices=sorted(FIXTURE_FILES))
    check_cmd = sub.add_parser("check", help="verify the frozen datasets")
    check_cmd.add_argument("datasets", nargs="*", choices=sorted(FIXTURE_FILES))
    args = parser.parse_args(argv)
    if args.command == "freeze":
        manifest = freeze(args.datasets)
        for name in args.datasets:
            entry = manifest["datasets"][name]
            print(f"{name}: {entry['items']} items, dev {entry['dev']} / heldout {entry['heldout']}")
        return 0
    names = args.datasets or sorted(read_manifest().get("datasets", {}))
    problems = [problem for name in names for problem in check(name)]
    for problem in problems:
        print(problem)
    if not problems:
        print("frozen: " + ", ".join(names))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
