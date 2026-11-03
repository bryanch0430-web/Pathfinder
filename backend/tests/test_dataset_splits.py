"""Proposal TC-06 (p4, l.132-135): 70/30 dev/held-out split, stratified, frozen before scoring."""

from __future__ import annotations

import copy
import math
from collections import Counter

import pytest

from app import dataset_splits
from app.dataset_splits import assign, check, content_hash, load_rows, read_manifest, stratum

FROZEN = ["routing", "injection", "hongkong", "japan"]


def test_tc06_manifest_freezes_the_custom_sets() -> None:
    """Proposal TC-06 (p4): the split is recorded with its seed and a hash of every frozen fixture."""
    manifest = read_manifest()
    assert manifest["seed"] == dataset_splits.SEED
    assert manifest["dev_share"] == 0.7
    assert set(FROZEN) <= set(manifest["datasets"])
    for name in FROZEN:
        entry = manifest["datasets"][name]
        rows = load_rows(name)
        assert entry["sha256"] == content_hash(rows)
        assert entry["items"] == len(rows) == entry["dev"] + entry["heldout"]


@pytest.mark.parametrize("name", FROZEN)
def test_tc06_frozen_sets_pass_the_split_check(name: str) -> None:
    """Proposal TC-06 (p4): every frozen fixture still matches its frozen, stratified split."""
    assert check(name) == []


@pytest.mark.parametrize("name", FROZEN)
def test_tc06_split_is_70_30_overall_and_inside_every_stratum(name: str) -> None:
    """Proposal TC-06 (p4): 70/30 dev/held-out, stratified (each stratum within one item of 30%)."""
    rows = load_rows(name)
    held = [row for row in rows if row["split"] == "heldout"]
    assert len(held) == math.floor(len(rows) * 0.3 + 0.5)
    per_stratum = Counter(stratum(name, row) for row in rows)
    held_stratum = Counter(stratum(name, row) for row in held)
    for key, size in per_stratum.items():
        share = size * 0.3
        assert math.floor(share) <= held_stratum[key] <= math.ceil(share), (key, size, held_stratum[key])


def test_tc06_editing_a_frozen_fixture_is_detected() -> None:
    """Proposal TC-06 (p4): a fixture changed after the freeze no longer passes the check."""
    rows = copy.deepcopy(load_rows("routing"))
    rows[0]["input"] += " (edited after the freeze)"
    assert any("changed after the split was frozen" in problem for problem in check("routing", rows))


def test_tc06_moving_an_item_between_splits_is_detected() -> None:
    """Proposal TC-06 (p4): a held-out item moved to dev is reported, so the held-out set cannot leak."""
    rows = copy.deepcopy(load_rows("routing"))
    victim = next(row for row in rows if row["split"] == "heldout")
    victim["split"] = "dev"
    problems = check("routing", rows)
    assert any(victim["id"] in problem for problem in problems)


def test_tc06_assignment_is_deterministic_and_stable_when_a_set_grows() -> None:
    """Proposal TC-06 (p4): the seeded assignment repeats exactly; growing a set moves few items."""
    rows = load_rows("routing")
    first = assign("routing", rows)
    assert first == assign("routing", copy.deepcopy(rows))
    grown = rows + [
        {"id": f"zz{index}", "input": "extra", "expected_intent": "ask", "destination": "Kyoto"}
        for index in range(4)
    ]
    second = assign("routing", grown)
    moved = [item for item, split in first.items() if second[item] != split]
    assert len(moved) <= 2


def test_tc06_unfrozen_dataset_is_reported() -> None:
    """Proposal TC-06 (p4): a set missing from the manifest is reported as not frozen."""
    problems = check("hongkong", manifest={"seed": dataset_splits.SEED, "dev_share": 0.7, "datasets": {}})
    assert problems and "not in" in problems[0]


@pytest.mark.parametrize("name", FROZEN)
def test_tc06_trial_report_shows_the_heldout_share_and_freeze(settings, name: str) -> None:
    """Proposal TC-06 (p4): POST /api/eval/trials reports the held-out share and that the split is frozen."""
    from fastapi.testclient import TestClient

    from app.interface.api import create_app

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/eval/trials",
            json={"dataset_id": name, "experiment_tag": "tc06"},
            headers={"X-Admin-Token": settings.admin_token},
        )
    assert response.status_code == 200
    metrics = {metric["key"]: metric for metric in response.json()["run"]["metrics"]}
    assert metrics["split_frozen"]["value"] == 100.0
    # Small sets cannot hit 30% exactly; the held-out count must be within one item of it.
    items = len(load_rows(name))
    assert abs(metrics["heldout_share"]["value"] / 100 * items - 0.3 * items) <= 1
