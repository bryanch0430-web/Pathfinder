"""Evaluation API: suites, cases, runs and the exported report."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.api.deps import container, require_admin
from app.eval import datasets
from app.eval import runner

router = APIRouter()


@router.get("/api/eval/suites")
async def suites(request: Request) -> dict[str, Any]:
    require_admin(request)
    return {"datasets": datasets.suite_list()}


@router.get("/api/eval/suites/{dataset_id}/cases")
async def suite_cases(dataset_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    try:
        return {"cases": datasets.preview(dataset_id)}
    except KeyError:
        raise HTTPException(status_code=404, detail="Dataset not found") from None


@router.post("/api/eval/suites/{dataset_id}/lock")
async def lock_suite(dataset_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    if dataset_id not in datasets.DATASETS:
        raise HTTPException(status_code=404, detail="Dataset not found")
    frozen = await c.store.get_kv("eval:frozen") or {}
    frozen[dataset_id] = True
    await c.store.set_kv("eval:frozen", frozen)
    return {"dataset_id": dataset_id, "frozen": True}


@router.get("/api/eval/reference")
async def reference(request: Request) -> dict[str, Any]:
    require_admin(request)
    return datasets.reference_catalog()


@router.get("/api/eval/trials")
async def trials(request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    runs = await c.store.list_eval_runs(50)
    return {"runs": runner.summaries(runs)}


@router.post("/api/eval/trials")
async def run_trial(request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    body = await request.json()
    dataset_id = str(body.get("dataset_id") or "")
    if dataset_id not in datasets.DATASETS:
        raise HTTPException(status_code=404, detail="Dataset not found")
    run = await runner.run_dataset(c, dataset_id, str(body.get("experiment_tag") or ""), str(body.get("split") or "all"))
    return {"run": run}


@router.get("/api/eval/trials/{run_id}")
async def get_trial(run_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    run = await c.store.get_eval_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"run": run}


@router.get("/api/eval/trials/{run_id}/export", response_class=PlainTextResponse)
async def export_trial(run_id: str, request: Request) -> str:
    require_admin(request)
    c = container(request)
    run = await c.store.get_eval_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return runner.report_text(run)


@router.get("/api/eval/contrast")
async def contrast(request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    runs = await c.store.list_eval_runs(60)
    latest: dict[str, dict[str, Any]] = {}
    for run in runs:
        key = f"{run['dataset_id']}:{run.get('experiment_tag', '')}"
        latest.setdefault(key, run)
    rows = []
    for run in latest.values():
        rows.append(
            {
                "run_id": run["id"],
                "dataset_id": run["dataset_id"],
                "tag": run.get("experiment_tag", ""),
                "metrics": run.get("metrics", []),
            }
        )
    return {"runs": rows}
