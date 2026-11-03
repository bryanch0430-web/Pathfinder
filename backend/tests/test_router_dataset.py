"""Proposal TC-04 (p4, l.132-133, 149-150): ~80 labelled utterances score the System 1 router alone."""

from __future__ import annotations

from collections import Counter

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.dataset_splits import load_rows
from app.interface.api import create_app

INTENTS = {"plan", "modify", "ask", "unclear"}
CITIES = {"Kyoto", "Tokyo", "Osaka", "Hong Kong"}
#: measured 76.2% (64/84) with the deterministic RuleRouter on 2026-11-03; floor = rounded down to 5%
RULE_ROUTER_FLOOR = 75.0


@pytest.fixture()
def routing_run(settings: Settings) -> dict:
    """One routing trial through POST /api/eval/trials, the code path the Evaluation page uses."""
    with TestClient(create_app(settings)) as client:
        client.headers["X-Admin-Token"] = settings.admin_token
        response = client.post("/api/eval/trials", json={"dataset_id": "routing", "experiment_tag": "tc04"})
        suites = client.get("/api/eval/suites").json()["datasets"]
    assert response.status_code == 200
    return {"run": response.json()["run"], "suites": suites}


def test_tc04_router_set_has_about_80_labelled_utterances_across_every_intent() -> None:
    """Proposal TC-04 (p4): about 80 utterances labelled new trip / modify / quick question / unclear."""
    rows = load_rows("routing")
    assert 75 <= len(rows) <= 90
    assert len({row["id"] for row in rows}) == len(rows)
    assert len({row["input"].strip().lower() for row in rows}) == len(rows)
    per_intent = Counter(row["expected_intent"] for row in rows)
    assert set(per_intent) == INTENTS
    assert min(per_intent.values()) >= 18
    assert {row["destination"] for row in rows} == CITIES


def test_tc04_routing_suite_lists_the_full_set(routing_run: dict) -> None:
    """Proposal TC-04 (p4): GET /api/eval/suites shows the expanded routing set."""
    suite = next(item for item in routing_run["suites"] if item["id"] == "routing")
    assert suite["item_count"] == len(load_rows("routing"))
    assert suite["frozen"] is True


def test_tc04_rule_router_accuracy_stays_above_the_measured_floor(routing_run: dict) -> None:
    """Proposal TC-04 (p4): the router is scored on the labelled set; regression floor at the measured value."""
    run = routing_run["run"]
    assert run["metrics"][0]["key"] == "routing_accuracy"
    assert len(run["cases"]) == len(load_rows("routing"))
    assert run["model_snapshot"]["router"] == "rules"
    assert run["metrics"][0]["value"] >= RULE_ROUTER_FLOOR


def test_tc04_trial_report_breaks_accuracy_down_by_split_and_intent(routing_run: dict) -> None:
    """Proposal TC-04 / TC-06 (p4): the trial report carries accuracy per frozen split and per intent."""
    run = routing_run["run"]
    metrics = {metric["key"]: metric["value"] for metric in run["metrics"]}
    cases = run["cases"]
    for split in ("dev", "heldout"):
        subset = [case for case in cases if case["split"] == split]
        assert subset
        expected = round(100.0 * sum(case["passed"] for case in subset) / len(subset), 1)
        assert metrics[f"routing_accuracy_{split}"] == expected
    labels = {row["id"]: row["expected_intent"] for row in load_rows("routing")}
    for intent in INTENTS:
        subset = [case for case in cases if labels[case["case_id"]] == intent]
        expected = round(100.0 * sum(case["passed"] for case in subset) / len(subset), 1)
        assert metrics[f"routing_accuracy_{intent}"] == expected
    assert all(case["judge_reason"].startswith("Expected ") for case in cases)


def test_tc04_tc10_rules_first_decisions_are_right_on_the_labelled_set(routing_run: dict) -> None:
    """Proposal TC-04 with TC-10 (p4): the decisions the router makes without a model call.

    The System 1 router lets the rules decide alone only on one unambiguous signal.
    On the labelled set every such decision is correct (48 of 84 measured on
    2026-11-03), so the shortcut that saves the routing call does not cost accuracy;
    the other utterances still go to Jev or the writer model.
    """
    run = routing_run["run"]
    metrics = {metric["key"]: metric["value"] for metric in run["metrics"]}
    alone = [case for case in run["cases"] if case["scores"]["rules_alone"]]
    assert metrics["rules_first_share"] == round(100.0 * len(alone) / len(run["cases"]), 1) >= 50.0
    assert metrics["rules_first_accuracy"] == 100.0
    assert all(case["passed"] for case in alone)
    assert {case["split"] for case in alone} == {"dev", "heldout"}
