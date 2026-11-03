"""``python -m app.export_contracts`` writes the two published contracts, and they are current.

The contracts are the only coupling between the backend and the frontend, so one that no longer
matches the code (a new header, a new field) misleads whoever reads it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import BASE_DIR

REGENERATE = "run `PYTHONPATH=backend python -m app.export_contracts` and commit the result"


@pytest.fixture(scope="module")
def exported(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Run the exporter the way a developer does: in a shell where ENVIRONMENT is not set."""
    target = tmp_path_factory.mktemp("contracts")
    env = {key: value for key, value in os.environ.items() if key != "ENVIRONMENT"}
    env["PYTHONPATH"] = str(BASE_DIR / "backend")
    done = subprocess.run(
        [sys.executable, "-m", "app.export_contracts", str(target)],
        env=env,
        cwd=target,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr[-1500:]
    return target


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_exporter_needs_no_environment_or_secrets(exported: Path) -> None:
    assert sorted(path.name for path in exported.iterdir()) == ["openapi.json", "trip_plan.schema.json"]
    assert "/api/turn/stream" in _read(exported / "openapi.json")["paths"]


@pytest.mark.parametrize("name", ["openapi.json", "trip_plan.schema.json"])
def test_the_committed_contract_matches_the_code(exported: Path, name: str) -> None:
    committed = _read(BASE_DIR / "contracts" / name)

    assert _read(exported / name) == committed, f"contracts/{name} is out of date: {REGENERATE}"
