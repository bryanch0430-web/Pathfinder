"""Fixtures for the evaluation-harness tests: tmp copies of the fixture files and the frozen
split, so a test can change a fixture without touching the real ones."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from backend.eval.datasets import FIXTURES_DIR
from backend.eval.split import SET_FILES, SPLIT_FILE


@pytest.fixture
def fixtures_copy(tmp_path: Path) -> Path:
    """tmp copy of the four fixture files plus splits/ (seed.json, custom_split.json)."""
    target = tmp_path / "fixtures"
    target.mkdir()
    for name in SET_FILES.values():
        shutil.copyfile(FIXTURES_DIR / name, target / name)
    shutil.copytree(SPLIT_FILE.parent, target / "splits")
    return target


def append_line(path: Path, line: str) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
