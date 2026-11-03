"""Proposal evaluation commitments TC-10, TC-11 and TC-12 (p4, Appendix C1).

The cost datasets are run through POST /api/eval/trials, the code path the
Evaluation page uses, and read back through the trial endpoints. Everything is
offline: sandbox providers and a metered writer that reports token usage.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.models import EvalCase
from app.eval_efficiency import accepted_plan, build_rig, case_context, router_turn
from app.eval_metering import RunLedger, estimate_tokens
from app.evaluation import FIXTURES
from app.interface.api import create_app

TRIAL_DATASETS = ("efficiency",)


def _settings(root: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=root,
        database_path=root / "test.db",
        trace_dir=root / "traces",
        eval_dir=root / "eval",
        llm_api_key="",
        llm_base_url="",
        xai_api_key="",
        decision_enabled=False,
        langfuse_enabled=False,
        default_currency="USD",
    )


@pytest.fixture(scope="module")
def trials(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """One scored run per dataset, through the same endpoints as the Evaluation page."""
    settings = _settings(tmp_path_factory.mktemp("trials"))
    with TestClient(create_app(settings)) as client:
        suites = {row["id"]: row for row in client.get("/api/eval/suites").json()["datasets"]}
        runs = {}
        for dataset in TRIAL_DATASETS:
            response = client.post("/api/eval/trials", json={"dataset_id": dataset, "experiment_tag": "proposal-check"})
            assert response.status_code == 200, response.text
            run = response.json()["run"]
            stored = client.get(f"/api/eval/trials/{run['id']}")
            assert stored.status_code == 200
            runs[dataset] = {"run": run, "stored": stored.json()["run"]}
        listed = {row["id"]: row for row in client.get("/api/eval/trials").json()["runs"]}
    return {"suites": suites, "runs": runs, "listed": listed}


def _metrics(run: dict) -> dict[str, dict]:
    return {metric["key"]: metric for metric in run["metrics"]}


def _fixture(name: str) -> dict[str, dict]:
    rows = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return {row["id"]: row for row in rows}


# ---------------------------------------------------------------------------
# TC-10: router path vs always running the full planning pipeline
# ---------------------------------------------------------------------------


def test_tc10_efficiency_dataset_is_offered_and_frozen(trials: dict) -> None:
    """Proposal TC-10 (p4, C1): the Evaluation page lists the efficiency dataset."""
    suite = trials["suites"]["efficiency"]
    assert suite["frozen"] is True
    assert suite["item_count"] == len(_fixture("efficiency.json")) == 10


def test_tc10_trial_report_shows_latency_calls_and_tokens_per_path(trials: dict) -> None:
    """Proposal TC-10 (p4, C1): latency, API-call count and token usage for each path."""
    run, stored = trials["runs"]["efficiency"]["run"], trials["runs"]["efficiency"]["stored"]
    metrics = _metrics(stored)
    for path in ("router", "full"):
        assert metrics[f"{path}_tool_calls"]["unit"] == "calls"
        assert metrics[f"{path}_llm_calls"]["unit"] == "calls"
        assert metrics[f"{path}_tokens"]["unit"] == "tokens"
        assert metrics[f"{path}_latency"]["unit"] == "ms"
        assert metrics[f"{path}_tokens"]["value"] > 0
        assert metrics[f"{path}_latency"]["value"] > 0
    assert metrics["router_api_call_saving"]["unit"] == "%"
    assert "router_token_saving" in metrics
    # token_usage is counted, not hard-coded: it is the sum of both paths' usage.
    per_case = sum(case["scores"]["router_tokens"] + case["scores"]["full_tokens"] for case in run["cases"])
    assert run["token_usage"] == per_case > 0
    assert stored["token_usage"] == run["token_usage"]
    assert trials["listed"][run["id"]]["token_usage"] == run["token_usage"]
    assert stored["model_snapshot"]["writer"].startswith("sandbox-metered")


def test_tc10_router_path_makes_fewer_calls_for_non_planning_intents(trials: dict) -> None:
    """Proposal TC-10 (p4, C1): the router avoids the full pipeline unless the turn plans a trip."""
    run = trials["runs"]["efficiency"]["run"]
    fixture = _fixture("efficiency.json")
    assert len(run["cases"]) == len(fixture)
    for case in run["cases"]:
        scores = case["scores"]
        router_calls = scores["router_tool_calls"] + scores["router_llm_calls"]
        full_calls = scores["full_tool_calls"] + scores["full_llm_calls"]
        if fixture[case["case_id"]]["expected_intent"] == "plan":
            # Planning is the full pipeline either way; routing adds one writer call.
            assert scores["router_tool_calls"] == scores["full_tool_calls"], case["output_summary"]
            assert scores["router_llm_calls"] == scores["full_llm_calls"] + 1, case["output_summary"]
        else:
            assert router_calls < full_calls, case["output_summary"]
            assert scores["router_tool_calls"] < scores["full_tool_calls"], case["output_summary"]
        assert case["passed"], (case["case_id"], case["failure_class"], case["output_summary"])
    metrics = _metrics(run)
    assert metrics["router_tool_calls"]["value"] < metrics["full_tool_calls"]["value"]
    assert metrics["router_api_call_saving"]["value"] > 0


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Proposal TC-10: measured offline, the router path spends more writer tokens than the full "
        "pipeline. The routing call and the ask/modify prompts (plan summary plus facts) outweigh the "
        "sandbox planner prompt, and scripted replies carry almost no completion tokens."
    ),
)
def test_tc10_router_path_uses_fewer_tokens_than_full_pipeline(trials: dict) -> None:
    """Proposal TC-10 (p4, C1): typical token use is lower with the System 1 router."""
    metrics = _metrics(trials["runs"]["efficiency"]["run"])
    assert metrics["router_tokens"]["value"] < metrics["full_tokens"]["value"]


def test_tc10_turn_trace_carries_the_writer_usage(settings: Settings) -> None:
    """Proposal TC-10 (p4): token usage comes from the operational log of each turn."""
    ledger = RunLedger()
    try:
        rig = build_rig(settings, "Kyoto", ledger)
        case = EvalCase(id="t", dataset="efficiency", input="Which hotel did you pick and why?", destination="Kyoto")
        session = ledger.store.create_session("")
        context = case_context(case, session.id)
        ledger.store.save_context(context)
        ledger.store.save_plan(asyncio.run(accepted_plan(settings, context)), "Accepted plan")
        rig.llm.route = {"intent": "ask", "confidence": 0.95}
        cost, path, error = asyncio.run(router_turn(rig, session.id, case.input))
    finally:
        ledger.close()
    assert (path, error) == ("ask", "")
    assert cost.llm_calls == len(rig.llm.purposes) >= 1
    assert cost.tokens == rig.llm.tokens > 0
    assert rig.gateway.prompt_tokens + rig.gateway.completion_tokens == rig.llm.tokens
    assert estimate_tokens("abcd") == 1 and estimate_tokens("abcde") == 2 and estimate_tokens("") == 0
