"""Frozen 70/30 development / held-out split of the custom sets.

Proposal: "The custom sets are split 70/30 into development and held-out, stratified by city and
constraint type, and the split is frozen before the first scored run. Held-out items are not used
to edit prompts."

What is split:
  * japan, hk (the custom scenario sets): 70/30, stratified by (city, constraint_type);
  * router (the 80 labelled utterances): 70/30, stratified by gold label, so router-prompt
    tuning can use dev utterances only;
  * probes (the 25 injection probes): NOT split. The proposal calls them "25 held-out probes",
    so every probe is held-out by definition.

Freezing: the assignment is written to fixtures/splits/custom_split.json together with a sha256
of every fixture file (newlines normalised, so a CRLF checkout does not count as a change).
`load_split()` refuses to silently re-split: if a fixture changed since the split was frozen it
raises `SplitFrozenError` unless re-freezing is explicitly requested
(`python -m backend.eval.split --refreeze`). The seed lives in fixtures/splits/seed.json.

Held-out guard: there is no prompt-editing mode in this harness. Any future mode that edits
prompts from scores must refuse `--split heldout` / `--split all` (proposal: "Held-out items are
not used to edit prompts"); `SplitFile.partition_of` is the lookup for that check.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from collections import Counter, defaultdict
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from backend.eval.datasets import (
    FIXTURES_DIR,
    HK_SCENARIOS_FILE,
    INJECTION_PROBES_FILE,
    JAPAN_SCENARIOS_FILE,
    ROUTER_LABELS_FILE,
    load_hk_scenarios,
    load_injection_probes,
    load_japan_scenarios,
    load_router_labels,
)
from backend.eval.models import Partition, SplitSelector
from backend.schemas.common import utcnow

SPLIT_DIR = FIXTURES_DIR / "splits"
SEED_FILE = SPLIT_DIR / "seed.json"
SPLIT_FILE = SPLIT_DIR / "custom_split.json"
DEV_FRACTION = 0.7

SET_FILES: dict[str, str] = {
    "japan": JAPAN_SCENARIOS_FILE,
    "hk": HK_SCENARIOS_FILE,
    "router": ROUTER_LABELS_FILE,
    "probes": INJECTION_PROBES_FILE,
}
STRATIFIED_BY: dict[str, str] = {
    "japan": "city|constraint_type",
    "hk": "city|constraint_type",
    "router": "label",
    "probes": "category (all held-out, not split)",
}


class SplitFrozenError(RuntimeError):
    """Fixtures changed after the split was frozen; re-freezing must be explicit."""


class StratumCount(BaseModel):
    dev: int = 0
    heldout: int = 0


class SetSplit(BaseModel):
    dev: list[str] = Field(default_factory=list)
    heldout: list[str] = Field(default_factory=list)
    strata: dict[str, StratumCount] = Field(default_factory=dict)


class SplitFile(BaseModel):
    version: int = 1
    seed: int
    dev_fraction: float
    created_at: datetime
    fixture_sha256: dict[str, str]
    stratified_by: dict[str, str]
    sets: dict[str, SetSplit]

    def partition_of(self, set_name: str, item_id: str) -> Partition | None:
        split = self.sets.get(set_name)
        if split is None:
            return None
        if item_id in split.dev:
            return Partition.DEV
        if item_id in split.heldout:
            return Partition.HELDOUT
        return None

    def ids(self, set_name: str, selector: SplitSelector) -> set[str]:
        split = self.sets.get(set_name, SetSplit())
        if selector is SplitSelector.DEV:
            return set(split.dev)
        if selector is SplitSelector.HELDOUT:
            return set(split.heldout)
        return set(split.dev) | set(split.heldout)


# ---- pure split logic ----------------------------------------------------------------------------


def dev_count(n: int, dev_fraction: float = DEV_FRACTION) -> int:
    """Items of an n-item stratum that go to dev: round-half-up of n*fraction, but a stratum
    with at least two items always keeps one item in each partition."""
    if n <= 0:
        return 0
    if n == 1:
        return 1
    return min(n - 1, max(1, math.floor(n * dev_fraction + 0.5)))


def stratified_split(
    items: Sequence[tuple[str, str]],
    *,
    seed: int,
    dev_fraction: float = DEV_FRACTION,
) -> SetSplit:
    """Split (item_id, stratum) pairs. Deterministic: `random.Random(seed)`, strata visited in
    sorted order, ids sorted before shuffling, so the same seed and items give the same split."""
    rng = random.Random(seed)
    by_stratum: dict[str, list[str]] = defaultdict(list)
    for item_id, stratum in items:
        by_stratum[stratum].append(item_id)
    out = SetSplit()
    for stratum in sorted(by_stratum):
        ids = sorted(by_stratum[stratum])
        rng.shuffle(ids)
        k = dev_count(len(ids), dev_fraction)
        out.dev.extend(ids[:k])
        out.heldout.extend(ids[k:])
        out.strata[stratum] = StratumCount(dev=k, heldout=len(ids) - k)
    out.dev.sort()
    out.heldout.sort()
    return out


def all_heldout(items: Sequence[tuple[str, str]]) -> SetSplit:
    out = SetSplit(heldout=sorted(i for i, _ in items))
    for stratum, count in sorted(Counter(s for _, s in items).items()):
        out.strata[stratum] = StratumCount(heldout=count)
    return out


def file_sha256(path: Path) -> str:
    text = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(text).hexdigest()


def fixture_hashes(fixtures_dir: Path = FIXTURES_DIR) -> dict[str, str]:
    return {name: file_sha256(fixtures_dir / name) for name in sorted(SET_FILES.values())}


def read_seed(seed_path: Path = SEED_FILE) -> int:
    data = json.loads(seed_path.read_text(encoding="utf-8"))
    seed = data.get("seed") if isinstance(data, dict) else None
    if not isinstance(seed, int):
        raise ValueError(f"{seed_path} must hold {{\"seed\": <int>}}")
    return seed


def build_split(fixtures_dir: Path, seed: int, dev_fraction: float = DEV_FRACTION) -> SplitFile:
    japan = [(s.id, f"{s.city}|{s.constraint_type.value}") for s in load_japan_scenarios(fixtures_dir)]
    hk = [(s.id, f"{s.city}|{s.constraint_type.value}") for s in load_hk_scenarios(fixtures_dir)]
    router = [(r.id, r.label.value) for r in load_router_labels(fixtures_dir)]
    probes = [(p.id, p.category.value) for p in load_injection_probes(fixtures_dir)]
    return SplitFile(
        seed=seed,
        dev_fraction=dev_fraction,
        created_at=utcnow(),
        fixture_sha256=fixture_hashes(fixtures_dir),
        stratified_by=dict(STRATIFIED_BY),
        sets={
            "japan": stratified_split(japan, seed=seed, dev_fraction=dev_fraction),
            "hk": stratified_split(hk, seed=seed, dev_fraction=dev_fraction),
            "router": stratified_split(router, seed=seed, dev_fraction=dev_fraction),
            "probes": all_heldout(probes),
        },
    )


def freeze_split(
    fixtures_dir: Path = FIXTURES_DIR,
    split_path: Path = SPLIT_FILE,
    seed_path: Path = SEED_FILE,
) -> SplitFile:
    split = build_split(fixtures_dir, read_seed(seed_path))
    split_path.parent.mkdir(parents=True, exist_ok=True)
    split_path.write_text(split.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    return split


def load_split(
    fixtures_dir: Path = FIXTURES_DIR,
    split_path: Path = SPLIT_FILE,
    *,
    refreeze: bool = False,
    seed_path: Path = SEED_FILE,
) -> SplitFile:
    """Return the frozen split. Never re-splits silently: a missing file or changed fixtures
    raise unless `refreeze=True` (the `--refreeze` CLI flag)."""
    if not split_path.is_file():
        if refreeze:
            return freeze_split(fixtures_dir, split_path, seed_path)
        raise FileNotFoundError(
            f"no frozen split at {split_path}; run `python -m backend.eval.split --refreeze`"
        )
    split = SplitFile.model_validate_json(split_path.read_text(encoding="utf-8"))
    current = fixture_hashes(fixtures_dir)
    changed = sorted(
        name for name in set(current) | set(split.fixture_sha256)
        if current.get(name) != split.fixture_sha256.get(name)
    )
    if changed:
        if refreeze:
            return freeze_split(fixtures_dir, split_path, seed_path)
        raise SplitFrozenError(
            "fixtures changed since the split was frozen ("
            + ", ".join(changed)
            + "); re-freeze explicitly with `python -m backend.eval.split --refreeze`"
        )
    return split


def summary_lines(split: SplitFile) -> list[str]:
    lines = [
        f"frozen split v{split.version}  seed={split.seed}  dev_fraction={split.dev_fraction}  "
        f"created_at={split.created_at.isoformat()}",
        f"{'set':<8} {'dev':>4} {'held':>5}  stratified by",
    ]
    for name, part in split.sets.items():
        lines.append(
            f"{name:<8} {len(part.dev):>4} {len(part.heldout):>5}  {split.stratified_by.get(name, '')}"
        )
    for name, part in split.sets.items():
        for stratum, count in part.strata.items():
            lines.append(f"  {name}:{stratum:<32} dev={count.dev} heldout={count.heldout}")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.eval.split",
        description="Show the frozen 70/30 split (or re-freeze it explicitly).",
    )
    parser.add_argument("--refreeze", action="store_true", help="re-split and overwrite the frozen file")
    parser.add_argument("--fixtures-dir", type=Path, default=FIXTURES_DIR)
    parser.add_argument("--split-file", type=Path, default=SPLIT_FILE)
    parser.add_argument("--seed-file", type=Path, default=SEED_FILE)
    args = parser.parse_args(argv)
    try:
        if args.refreeze:
            split = freeze_split(args.fixtures_dir, args.split_file, args.seed_file)
            print(f"re-froze split -> {args.split_file}")
        else:
            split = load_split(args.fixtures_dir, args.split_file, seed_path=args.seed_file)
    except (SplitFrozenError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print("\n".join(summary_lines(split)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
