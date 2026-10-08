"""Scenario 21: `python -m backend.eval.run` selects items with the frozen split, writes one JSON
report per metric plus summary.json, and refuses to run on changed fixtures."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from backend.eval.datasets import HK_SCENARIOS_FILE
from backend.eval.models import SplitSelector
from backend.eval.run import METRICS, main, parse_metrics, select
from backend.eval.split import load_split

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_s21_parse_metrics_accepts_repeats_comma_lists_and_all() -> None:
    assert parse_metrics(None) == list(METRICS)
    assert parse_metrics(["injection,routing", "routing"]) == ["routing", "injection"]
    assert parse_metrics(["all"]) == list(METRICS)
    with pytest.raises(ValueError, match="unknown metric"):
        parse_metrics(["speed"])


def test_s21_selection_follows_the_frozen_split() -> None:
    split = load_split()
    held = select(split, SplitSelector.HELDOUT)
    dev = select(split, SplitSelector.DEV)
    assert {r.id for r in held.router} == split.ids("router", SplitSelector.HELDOUT)
    assert len(held.probes) == 25 and dev.probes == []
    assert not {s.id for s in held.custom} & {s.id for s in dev.custom}
    assert len(held.custom) + len(dev.custom) == 33


def test_s21_cli_writes_reports_and_summary(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "reports"
    code = main(["--metric", "routing,recovery", "--metric", "injection", "--split", "dev", "--out", str(out)])
    assert code == 0
    assert sorted(p.name for p in out.iterdir()) == ["injection.json", "recovery.json", "routing.json", "summary.json"]
    summary = json.loads((out / "summary.json").read_text())
    assert summary["split"] == "dev" and summary["metrics"] == ["routing", "recovery", "injection"]
    rows = {r["metric"]: r for r in summary["rows"]}
    assert rows["routing"]["n"] == 56 and rows["recovery"]["n"] == 10 and rows["injection"]["n"] == 0
    routing = json.loads((out / "routing.json").read_text())
    assert routing["metric"] == "routing" and len(routing["items"]) == 56
    printed = capsys.readouterr().out
    assert printed.splitlines()[0].startswith("metric") and "routing" in printed
    assert "held-out by definition" in printed


def test_s21_cli_fail_under_target(tmp_path: Path) -> None:
    # The default (both models compliant) injection run misses the assumed 100% target.
    assert main(["--metric", "injection", "--fail-under-target"]) == 1
    assert main(["--metric", "injection"]) == 0


def test_s21_cli_exits_non_zero_on_a_changed_fixture(
    fixtures_copy: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hk = fixtures_copy / HK_SCENARIOS_FILE
    lines = hk.read_text(encoding="utf-8").splitlines()
    hk.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")  # drop one scenario
    args = ["--fixtures-dir", str(fixtures_copy), "--split-file", str(fixtures_copy / "splits" / "custom_split.json")]
    assert main(["--metric", "routing", *args]) == 2
    err = capsys.readouterr().err
    assert "fixtures changed since the split was frozen" in err and HK_SCENARIOS_FILE in err


def test_s21_cli_module_entry_point(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "backend.eval.run", "--metric", "routing", "--split", "dev", "--out", str(tmp_path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert (tmp_path / "routing.json").is_file() and "routing" in proc.stdout
