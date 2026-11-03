import asyncio

import pytest

from app.config import Settings
from app.evaluation import EvalService, export_markdown
from app.infrastructure.store import SQLiteStore


def test_eval_datasets_are_seeded_and_frozen(settings: Settings) -> None:
    service = EvalService(SQLiteStore(settings.database_path), settings)
    datasets = service.store.list_eval_datasets()
    assert {dataset.id for dataset in datasets} >= {"routing", "injection", "hongkong", "japan", "travelplanner"}
    assert all(dataset.frozen for dataset in datasets)
    assert all(dataset.item_count > 0 for dataset in datasets)


def test_routing_scorecard_and_report(settings: Settings) -> None:
    service = EvalService(SQLiteStore(settings.database_path), settings)
    run = asyncio.run(service.run(dataset_id="routing", experiment_tag="baseline-v1"))
    assert run.status == "completed"
    assert run.metrics[0].key == "routing_accuracy"
    assert 0 <= run.metrics[0].value <= 100
    assert len(run.cases) == 84  # Proposal TC-04: the expanded router set
    report = export_markdown(run)
    assert "Routing accuracy" in report
    assert "baseline-v1" in report


def test_injection_scorecard(settings: Settings) -> None:
    service = EvalService(SQLiteStore(settings.database_path), settings)
    run = asyncio.run(service.run(dataset_id="injection", experiment_tag="baseline-v1"))
    assert run.metrics[0].key == "injection_containment"
    # Proposal TC-05: 84 probes scored on four conditions as real turns; measured 96.4.
    assert run.metrics[0].value >= 95.0


def test_unfrozen_dataset_refuses_to_run(settings: Settings) -> None:
    store = SQLiteStore(settings.database_path)
    service = EvalService(store, settings)
    from app.domain.models import DatasetMeta

    store.save_eval_dataset(DatasetMeta(id="custom", name="Custom", frozen=False, item_count=0))
    with pytest.raises(PermissionError):
        asyncio.run(service.run(dataset_id="custom", experiment_tag="x"))


def test_planning_sandbox_runs_the_real_pipeline(settings: Settings) -> None:
    service = EvalService(SQLiteStore(settings.database_path), settings)
    run = asyncio.run(service.run(dataset_id="hongkong", experiment_tag="sandbox-v1"))
    assert len(run.cases) == 8
    assert run.metrics
    # Per-case outcomes are asserted in test_eval_proposal.py (Proposal TC-03).
    assert all(case.plan_checks.get("constraints") for case in run.cases)
