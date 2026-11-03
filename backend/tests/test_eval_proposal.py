"""Proposal evaluation commitments TC-02, TC-03, TC-07, TC-08 and TC-09 (pp4-8).

The datasets run through POST /api/eval/trials, the same path the Evaluation page
uses, so every field asserted here is what the page and its export receive. All runs
use the offline sandbox: no network, no model key.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.application.llm_gateway import LLMGateway
from app.application.modification import ModifyPath
from app.application.planner import Planner
from app.application.planning import PlanPath
from app.application.prompts import PromptLibrary
from app.config import Settings
from app.domain.errors import ProviderError
from app.domain.models import EvalCase, GeoPoint, PlanItem, SourceRef, TripContext
from app.eval_checks import (
    RecordingToolbox,
    ToolLog,
    classify,
    disruption_outcomes,
    grounding,
    judge_family_check,
    model_family,
    score_plan,
    short_transfers,
)
from app.evaluation import FIXTURES, PLACES, EvalService, SandboxRegistry
from app.infrastructure.judge import JudgeVerdict, _case_payload
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer
from app.infrastructure.store import SQLiteStore
from app.interface.api import create_app

PLANNING_SETS = {"japan": 6, "hongkong": 8, "travelplanner": 3}


def _settings(root: Path, **extra) -> Settings:
    root.mkdir(parents=True, exist_ok=True)
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
        **extra,
    )


@pytest.fixture(scope="module")
def trials(tmp_path_factory) -> dict:
    """One POST /api/eval/trials per planning set, as the Evaluation page sends it."""
    settings = _settings(tmp_path_factory.mktemp("trials"))
    with TestClient(create_app(settings)) as client:
        suites = {row["id"]: row for row in client.get("/api/eval/suites").json()["datasets"]}
        runs = {}
        exports = {}
        for name in PLANNING_SETS:
            response = client.post("/api/eval/trials", json={"dataset_id": name, "experiment_tag": "proposal"})
            assert response.status_code == 200, response.text
            runs[name] = response.json()["run"]
            exports[name] = client.get(f"/api/eval/trials/{runs[name]['id']}/export").text
    return {"suites": suites, "runs": runs, "exports": exports}


@pytest.fixture(scope="module")
def unhandled(tmp_path_factory) -> dict:
    """The Hong Kong set with the HKO warnings and MTR status handling switched off."""
    settings = _settings(tmp_path_factory.mktemp("unhandled"), enable_hk_disruptions=False)
    with TestClient(create_app(settings)) as client:
        response = client.post("/api/eval/trials", json={"dataset_id": "hongkong", "experiment_tag": "no-hk-handling"})
        assert response.status_code == 200, response.text
        return {"runs": {"hongkong": response.json()["run"]}}


def _case(trials: dict, dataset: str, case_id: str) -> dict:
    return next(row for row in trials["runs"][dataset]["cases"] if row["case_id"] == case_id)


def _metrics(trials: dict, dataset: str) -> dict:
    return {row["key"]: row for row in trials["runs"][dataset]["metrics"]}


def _constraint(case: dict, kind: str) -> list[dict]:
    return [row for row in case["plan_checks"]["constraints"] if row["kind"] == kind]


def _sandbox_plan(settings: Settings, destination: str, *, days: int = 2, constraints=(), fault: str = "", detail=None):
    """Plan one trip on the sandbox with the eval's recording toolbox."""
    settings = settings.model_copy(update={"preplan_budget_seconds": 15, "agent_timeout_seconds": 8})
    start = date.today() + timedelta(days=30)
    registry = SandboxRegistry(settings, destination, fault=fault, fault_detail=detail or {}, start=start)
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    plan_path = PlanPath(planner=Planner(gateway, settings, registry.fx), registry=registry, settings=settings)
    context = TripContext(
        session_id="t", destination=destination, origin="Hong Kong", start_date=start,
        end_date=start + timedelta(days=days - 1), days=days, party_size=2, budget=900000,
        currency="JPY", hard_constraints=list(constraints),
    )
    log = ToolLog()
    plan = asyncio.run(
        plan_path.run(
            context=context, session_id="t", toolbox=RecordingToolbox(log), tracer=NoopTracer(), emit=lambda *_: None
        )
    )
    return plan, log, registry, plan_path, gateway, context, start


# --- the datasets in the Evaluation feature --------------------------------


def test_tc02_tc03_datasets_are_listed_with_their_sizes(trials: dict) -> None:
    """Proposal TC-02/TC-03 (p4): the Japan and Hong Kong sets appear in GET /api/eval/suites."""
    for name, size in PLANNING_SETS.items():
        assert trials["suites"][name]["item_count"] == size
        assert trials["suites"][name]["frozen"] is True
        assert len(trials["runs"][name]["cases"]) == size
    for name in PLANNING_SETS:
        rows = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
        assert {row["split"] for row in rows} == {"dev", "heldout"}


def test_eval_trials_report_the_proposal_fields(trials: dict) -> None:
    """Proposal TC-02/03/07/08/09 (pp4-7): the trial report carries every new score."""
    for name in PLANNING_SETS:
        run = trials["runs"][name]
        metrics = _metrics(trials, name)
        assert {"constraint_satisfaction", "constraints_met", "grounding"} <= set(metrics)
        assert metrics["constraint_satisfaction"]["label"] == "Constraint satisfaction (met/total)"
        assert run["model_snapshot"]["judge_check"]["same_family"] in (True, False)
        for case in run["cases"]:
            checks = case["plan_checks"]
            assert checks["constraints_total"] == len(checks["constraints"]) > 0
            assert 0 <= checks["constraints_met"] <= checks["constraints_total"]
            assert {"items", "grounded", "ungrounded", "score"} <= set(checks["grounding"])
            assert checks["tool_log"]["calls"]
            assert case["judge_reason"].startswith("Constraints ")
        assert "## Plan checks" in trials["exports"][name]
    japan = _metrics(trials, "japan")
    assert {"constraint_must_include", "constraint_dietary"} <= set(japan)
    hongkong = _metrics(trials, "hongkong")
    assert {
        "error_recovery",
        "recovery_typhoon",
        "recovery_venue_closure",
        "recovery_transit_delay",
        "recovery_source_timeout",
        "constraint_weather",
        "constraint_exclude",
        "constraint_travel_times",
    } <= set(hongkong)
    for case in trials["runs"]["hongkong"]["cases"]:
        if case["case_id"] != "hk04":
            assert case["plan_checks"]["recovery"]["handled"] in (True, False)
            assert "error_recovery" in case["scores"]


# --- TC-02: Japan set -------------------------------------------------------


def test_tc02_japan_set_scores_must_include_and_vegetarian(trials: dict) -> None:
    """Proposal TC-02 (p4): must-include and vegetarian constraints are scored per case."""
    assert [row["met"] for row in _constraint(_case(trials, "japan", "jp01"), "must_include")] == [True]
    jp04 = _case(trials, "japan", "jp04")
    assert [row["met"] for row in _constraint(jp04, "must_include")] == [True, True]
    for case_id in ("jp03", "jp05"):
        diet = _constraint(_case(trials, "japan", case_id), "dietary")
        assert len(diet) == 1 and diet[0]["met"], diet
        assert "vegetarian per the place data" in diet[0]["evidence"]
    for case in trials["runs"]["japan"]["cases"]:
        assert case["passed"], (case["case_id"], case["judge_reason"])
    assert _metrics(trials, "japan")["constraint_dietary"]["value"] == 100.0


def test_tc02_must_include_survives_more_candidates_than_slots(trials: dict) -> None:
    """Proposal TC-02 (p4): jp04's Ghibli Museum is the last-ranked of 10 Tokyo candidates."""
    assert [row[0] for row in PLACES["Tokyo"]][-1] == "Ghibli Museum"
    assert len(PLACES["Tokyo"]) > 2 * 4, "a 2-day trip cannot hold every Tokyo candidate"
    evidence = _constraint(_case(trials, "japan", "jp04"), "must_include")[0]["evidence"]
    assert evidence == "Planned: Ghibli Museum."


def test_tc02_vegetarian_check_flags_a_meat_restaurant(settings: Settings) -> None:
    """Proposal TC-02 (p4): a meal whose place data is not vegetarian fails the constraint."""
    plan, log, *_ = _sandbox_plan(settings, "Osaka", days=2)
    case = EvalCase(id="x", dataset="japan", input="", days=2, constraints=["vegetarian meals only"])
    row = _constraint_rows(score_plan(case, plan=plan, initial=plan, log=log, start=plan.dates.start))
    assert row["dietary"]["met"] is False
    assert "Dotonbori Kushikatsu" in row["dietary"]["evidence"]


def test_tc02_planner_keeps_meals_to_the_requested_diet(settings: Settings) -> None:
    """Proposal TC-02 (p4): with a vegetarian constraint the planner offers no other meal."""
    plan, log, *_ = _sandbox_plan(settings, "Osaka", days=2, constraints=["vegetarian meals only"])
    meals = [item.name for _, item in plan.iter_items() if item.type.value == "meal"]
    assert meals and set(meals) <= {"Paprika Shokudo Vegan", "Osaka Vegetarian Kitchen", "Shitennoji Shojin Ryori"}
    kyoto, *_ = _sandbox_plan(settings, "Kyoto", days=2, constraints=["vegetarian meals only"])
    assert not [item for _, item in kyoto.iter_items() if item.type.value == "meal"]
    assert any(gap.section == "meals" and "vegetarian" in gap.message for gap in kyoto.data_quality.gaps)


# --- TC-03: Hong Kong disruptions -------------------------------------------


def test_tc03_each_disruption_has_its_own_simulation(settings: Settings) -> None:
    """Proposal TC-03 (p4): typhoon, closure, MTR delay and source timeout are injected differently."""
    start = date(2027, 3, 12)
    geo = GeoPoint(lat=22.28, lng=114.16)

    typhoon = SandboxRegistry(settings, "Hong Kong", fault="typhoon", fault_detail={"day": 2}, start=start)
    days = asyncio.run(typhoon.weather[0].forecast(geo, start, start + timedelta(days=2)))
    assert ["Typhoon" in day.summary for day in days] == [False, True, False]
    # The HKO warnings feed has the signal in force on that day only.
    alerts = asyncio.run(typhoon.warnings[0].alerts(start=start, end=start + timedelta(days=2)))
    assert [(alert.date, alert.code, alert.severe) for alert in alerts] == [(start + timedelta(days=1), "TC8", True)]

    closure = SandboxRegistry(settings, "Hong Kong", fault="venue_closure", fault_detail={"venue": "Peak Tram"})
    search = dict(destination="Hong Kong", geo=geo, category="attraction", limit=10)
    assert "Peak Tram" in [place.name for place in asyncio.run(closure.places[0].search(**search))]
    closure.close_venue("Peak Tram")
    assert "Peak Tram" not in [place.name for place in asyncio.run(closure.places[0].search(**search))]

    delay = SandboxRegistry(settings, "Hong Kong", fault="transit_delay", fault_detail={"delay_minutes": 40})
    normal = SandboxRegistry(settings, "Hong Kong")
    far = GeoPoint(lat=22.30, lng=114.17)
    delayed = asyncio.run(delay.routes[0].leg(geo, far, mode="transit"))
    usual = asyncio.run(normal.routes[0].leg(geo, far, mode="transit"))
    assert delayed.duration_minutes == usual.duration_minutes + 40 and "delay" in delayed.source
    walk = asyncio.run(delay.routes[0].leg(geo, far, mode="walk"))
    assert walk.source == "estimate"
    # The MTR status feed reports the line (Island Line by default) delayed on its
    # day, which is day 1 (today without a start date); other lines run normally.
    today = date.today()
    rows = asyncio.run(delay.transit[0].status(lines=["ISL", "TWL"], start=today, end=today + timedelta(days=1)))
    assert [(row.line, row.delayed, row.delay_minutes) for row in rows] == [("ISL", True, 40), ("TWL", False, None)]
    assert asyncio.run(delay.transit[0].status(lines=["ISL"], start=today + timedelta(days=1), end=today + timedelta(days=2))) == []
    for quiet in (normal, closure):
        assert asyncio.run(quiet.warnings[0].alerts(start=today, end=today + timedelta(days=400))) == []
        assert asyncio.run(quiet.transit[0].status(lines=["ISL"], start=today, end=today + timedelta(days=400))) == []

    timeout = SandboxRegistry(settings, "Hong Kong", fault="source_timeout")
    with pytest.raises(ProviderError):
        asyncio.run(timeout.places[0].search(**search))
    assert asyncio.run(timeout.places[0].search(**search))

    for registry in (typhoon, closure, delay):
        assert len(registry.places) == 1, "only source_timeout adds the failing place source"


HK_EXPECTED_HANDLING = {
    "typhoon": {"alert_detected", "alert_recorded", "outdoor_stops_moved", "constraints_still_hold"},
    "venue_closure": {
        "venue_in_initial_plan",
        "invalid_item_removed",
        "alternative_fetched",
        "failure_recorded",
        "constraints_still_hold",
    },
    "transit_delay": {"delay_detected", "transfers_absorb_delay", "delay_recorded", "constraints_still_hold"},
    "source_timeout": {"failure_detected", "alternative_fetched", "failure_recorded", "constraints_still_hold"},
}


def test_tc03_every_disruption_reports_its_own_outcomes(trials: dict) -> None:
    """Proposal TC-03 (p4): each fault kind is scored on its own expected handling."""
    rows = json.loads((FIXTURES / "hongkong.json").read_text(encoding="utf-8"))
    kinds = {row["id"]: row.get("fault", "") for row in rows}
    assert sorted(set(kinds.values()) - {""}) == sorted(HK_EXPECTED_HANDLING)
    for case_id, fault in kinds.items():
        disruption = _case(trials, "hongkong", case_id)["plan_checks"]["disruption"]
        if not fault:
            assert disruption is None
            continue
        assert disruption["kind"] == fault
        assert set(disruption["outcomes"]) == HK_EXPECTED_HANDLING[fault]
        assert set(disruption["evidence"]) <= set(disruption["outcomes"])




@pytest.mark.parametrize(
    "case_id",
    [
        "hk01",
        "hk02",
        "hk03",
        "hk04",
        "hk05",
        "hk06",
        "hk07",
        "hk08",
    ],
)
def test_tc03_hongkong_case_passes(trials: dict, case_id: str) -> None:
    """Proposal TC-03 (p4): every Hong Kong case is asserted on its own."""
    case = _case(trials, "hongkong", case_id)
    assert case["passed"], case["judge_reason"]


def test_tc03_failing_cases_name_the_unhandled_disruption(unhandled: dict) -> None:
    """Proposal TC-03 (p4): a case that fails says which handling is missing.

    The run switches the HKO warnings and MTR status handling off, so the typhoon and
    MTR cases fail the way they did before that handling existed.
    """
    for case_id in ("hk01", "hk08"):
        case = _case(unhandled, "hongkong", case_id)
        outcomes = case["plan_checks"]["disruption"]["outcomes"]
        assert outcomes["alert_detected"] and outcomes["alert_recorded"]
        assert not outcomes["outdoor_stops_moved"]
        assert case["failure_class"] == "unmet_weather"
        assert "Outdoor stops on an alert day" in case["judge_reason"]
    # hk03: the 40-minute delay makes day-1 metro transfers too short. hk06: the
    # delay day has only walking legs, so travel times hold, but the delay is
    # neither detected nor recorded.
    for case_id, failure in (("hk03", "unmet_travel_times"), ("hk06", "transit_delay_not_handled")):
        case = _case(unhandled, "hongkong", case_id)
        outcomes = case["plan_checks"]["disruption"]["outcomes"]
        assert not outcomes["delay_detected"] and not outcomes["delay_recorded"]
        assert case["failure_class"] == failure
    assert not _case(unhandled, "hongkong", "hk03")["plan_checks"]["disruption"]["outcomes"]["transfers_absorb_delay"]
    metrics = _metrics(unhandled, "hongkong")
    assert metrics["recovery_typhoon"]["value"] == metrics["recovery_transit_delay"]["value"] == 0.0


def test_tc03_typhoon_day_has_no_outdoor_stops(trials: dict) -> None:
    """Proposal TC-03 (p4): on a typhoon-signal day only indoor stops remain."""
    for case_id in ("hk01", "hk08"):
        case = _case(trials, "hongkong", case_id)
        assert case["plan_checks"]["disruption"]["outcomes"]["outdoor_stops_moved"]
        assert case["plan_checks"]["disruption"]["handled"]
        assert case["plan_checks"]["tool_log"]["calls"]["weather.warnings:ok"] == 1
    assert _metrics(trials, "hongkong")["recovery_typhoon"]["value"] == 100.0
    assert _metrics(trials, "hongkong")["constraint_weather"]["value"] == 100.0


def test_tc03_mtr_delay_is_detected_and_absorbed(trials: dict) -> None:
    """Proposal TC-03 (p4): an MTR delay is detected and the day's transfers allow for it."""
    for case_id in ("hk03", "hk06"):
        case = _case(trials, "hongkong", case_id)
        outcomes = case["plan_checks"]["disruption"]["outcomes"]
        assert outcomes["delay_detected"] and outcomes["transfers_absorb_delay"] and outcomes["delay_recorded"]
        assert case["plan_checks"]["disruption"]["handled"]
        assert case["plan_checks"]["tool_log"]["calls"]["routes.status:ok"] == 1
    assert _metrics(trials, "hongkong")["recovery_transit_delay"]["value"] == 100.0


# --- TC-07: constraint satisfaction -----------------------------------------


def _constraint_rows(score) -> dict:
    return {row["kind"]: row for row in score.constraints}


def test_tc07_constraint_score_is_met_over_total(trials: dict, unhandled: dict) -> None:
    """Proposal TC-07 (p4): the score is met / total constraints, not one binary per case."""
    hk01 = _case(unhandled, "hongkong", "hk01")  # outdoor stops left on the typhoon day
    checks = hk01["plan_checks"]
    assert (checks["constraints_met"], checks["constraints_total"]) == (3, 4)
    assert hk01["scores"]["constraint_satisfaction"] == 0.75
    handled = _case(trials, "hongkong", "hk01")["plan_checks"]
    assert (handled["constraints_met"], handled["constraints_total"]) == (4, 4)
    for name in PLANNING_SETS:
        cases = trials["runs"][name]["cases"]
        met = sum(case["plan_checks"]["constraints_met"] for case in cases)
        total = sum(case["plan_checks"]["constraints_total"] for case in cases)
        metric = _metrics(trials, name)["constraints_met"]
        assert metric["value"] == met and metric["label"] == f"Constraints met (of {total})"
        mean = sum(case["scores"]["constraint_satisfaction"] for case in cases) / len(cases)
        assert _metrics(trials, name)["constraint_satisfaction"]["value"] == round(100 * mean, 1)


def test_tc07_every_fixture_constraint_has_a_deterministic_check() -> None:
    """Proposal TC-07 (p6): each written constraint maps to a script, none is left to a judge."""
    for name in PLANNING_SETS:
        for row in json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8")):
            for text in row["constraints"]:
                assert classify(text)[0] != "unchecked", (row["id"], text)
    assert classify("must include the Ghibli Museum") == ("must_include", "the Ghibli Museum")
    assert classify("do not include the closed Peak Tram") == ("exclude", "the closed Peak Tram")
    assert classify("vegetarian meals only") == ("dietary", "vegetarian")
    assert classify("avoid outdoor stops during the typhoon signal") == ("weather", "outdoor")
    assert classify("keep travel times realistic") == ("travel_times", "")


def test_tc07_an_undecidable_constraint_counts_against_the_score(settings: Settings) -> None:
    """Proposal TC-07 (p6): a constraint no script decides is unmet until a judge decides it."""
    plan, log, *_ = _sandbox_plan(settings, "Kyoto", days=2)
    case = EvalCase(id="x", dataset="japan", input="", days=2, constraints=["the hotel needs a rooftop pool"])
    score = score_plan(case, plan=plan, initial=plan, log=log, start=plan.dates.start)
    assert (score.met, score.total) == (1, 2)
    assert _constraint_rows(score)["unchecked"]["evidence"].endswith("it needs a judge.")


def test_tc07_travel_time_check_uses_the_transport_model(settings: Settings) -> None:
    """Proposal TC-07 (p4): "keep travel times realistic" is checked stop to stop, delays included."""
    plan, *_ = _sandbox_plan(settings, "Hong Kong", days=2)
    attractions_only = [line for line in short_transfers(plan) if "Kitchen" not in line and "Cafe" not in line]
    assert attractions_only == [], "the planner times attraction-to-attraction transfers itself"
    first = plan.days[0].date.isoformat()
    assert len(short_transfers(plan, delay_dates={first}, delay=40)) > len(short_transfers(plan))


def test_tc07_meal_stops_leave_time_for_the_transfer(settings: Settings) -> None:
    """Proposal TC-07 (p4): lunch and dinner leave time to get there from the previous stop."""
    plan, *_ = _sandbox_plan(settings, "Hong Kong", days=2)
    assert short_transfers(plan) == []


def test_tc07_run_report_flags_a_same_family_judge(tmp_path: Path) -> None:
    """Proposal TC-07 (p6): the report flags a judge from the writer's model family."""
    assert model_family("deepseek-chat") == "deepseek"
    assert model_family("grok-4") == "xai"
    assert model_family("gpt-4o", "https://api.openai.com/v1") == "openai"

    async def fake_score(**_):
        return JudgeVerdict(score=4, flags=[], reason="Looks grounded.")

    same = _settings(tmp_path / "same", judge_api_key="test-key")
    assert (same.judge_model, same.llm_model) == ("deepseek-chat", "deepseek-chat"), "defaults are unchanged"
    service = EvalService(SQLiteStore(same.database_path), same)
    service.judge.score = fake_score  # no network: the judge call is stubbed
    run = asyncio.run(service.run(dataset_id="travelplanner", experiment_tag="judge"))
    check = run.model_snapshot["judge_check"]
    assert check["same_family"] is True and check["judge_enabled"] is True
    assert "same model family (deepseek)" in check["warning"]
    assert {metric.key: metric.value for metric in run.metrics}["judge_independence"] == 0.0
    assert all("judge shares the writer's model family" in case.judge_flags for case in run.cases)

    other = _settings(tmp_path / "other", judge_api_key="test-key", judge_model="gpt-4o-mini", judge_base_url="https://api.openai.com/v1")
    service = EvalService(SQLiteStore(other.database_path), other)
    service.judge.score = fake_score
    run = asyncio.run(service.run(dataset_id="travelplanner", experiment_tag="judge"))
    assert run.model_snapshot["judge_check"]["same_family"] is False
    assert {metric.key: metric.value for metric in run.metrics}["judge_independence"] == 100.0
    assert not any("model family" in flag for case in run.cases for flag in case.judge_flags)

    off = judge_family_check(_settings(tmp_path / "off"), judge_enabled=False)
    assert off["same_family"] is True and off["warning"].startswith("The judge is off.")


def test_tc07_judge_payload_reads_the_plan(settings: Settings) -> None:
    """Proposal TC-07 (p6): the judge is shown the plan's real items and cost."""
    plan, *_ = _sandbox_plan(settings, "Kyoto", days=2)
    case = EvalCase(id="x", dataset="japan", input="Kyoto", days=2)
    payload = json.loads(_case_payload(case, plan, [], {}))
    first = plan.days[0].items[0]
    assert any(first.name in row for row in payload["plan"]["days"][0]["items"])
    assert payload["plan"]["cost"] == {"total": plan.cost.total, "currency": plan.cost.currency, "status": plan.cost.status}


# --- TC-08: error recovery --------------------------------------------------


def test_tc08_closed_venue_is_replaced(trials: dict) -> None:
    """Proposal TC-08 (p4): the closed venue is gone, an alternative was fetched, the failure recorded."""
    for case_id, venue in (("hk02", "Peak Tram"), ("hk07", "Tai Kwun")):
        case = _case(trials, "hongkong", case_id)
        recovery = case["plan_checks"]["recovery"]
        assert recovery == {
            "handled": True,
            "venue_in_initial_plan": True,
            "invalid_item_removed": True,
            "alternative_fetched": True,
            "failure_recorded": True,
            "constraints_still_hold": True,
        }
        assert case["scores"]["error_recovery"] == 1.0
        assert f"closure: {venue} was reported closed." in case["plan_checks"]["disruption"]["evidence"]["failure_recorded"]
        assert _constraint(case, "exclude")[0]["met"]
    assert _metrics(trials, "hongkong")["recovery_venue_closure"]["value"] == 100.0


def test_tc08_closure_is_recorded_on_the_plan(settings: Settings) -> None:
    """Proposal TC-08 (p4): the edit path records the closure and keeps a valid stop."""
    plan, log, registry, plan_path, gateway, context, _ = _sandbox_plan(settings, "Hong Kong", days=2)
    assert any(item.name == "Peak Tram" for _, item in plan.iter_items())
    registry.close_venue("Peak Tram")
    modify = ModifyPath(planner=plan_path.planner, plan_path=plan_path, settings=plan_path.settings, gateway=gateway)
    updated, _ = asyncio.run(
        modify.run(
            plan=plan,
            context=context,
            message="The Peak Tram is closed today. Please replace the Peak Tram with a different stop.",
            route_parts=["attraction"],
            toolbox=RecordingToolbox(log, path="modify"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )
    assert not any(item.name == "Peak Tram" for _, item in updated.iter_items())
    [entry] = updated.disruptions
    assert entry.kind == "closure" and entry.resolved and entry.summary == "Peak Tram was reported closed."
    found = updated.find_item(entry.affected_item_ids[0])
    assert found and isinstance(found[1].geo, GeoPoint)


def test_tc08_source_timeout_falls_back_and_is_recorded(trials: dict, settings: Settings) -> None:
    """Proposal TC-08 (p4): a timed-out source is logged, a fallback answers, the plan notes it."""
    recovery = _case(trials, "hongkong", "hk05")["plan_checks"]["recovery"]
    assert recovery["handled"] and recovery["failure_detected"] and recovery["failure_recorded"]
    plan, log, *_ = _sandbox_plan(settings, "Hong Kong", fault="source_timeout")
    assert any("fallback source answered" in note for note in plan.data_quality.notes)
    assert log.summary()["errors"] == ["places.search:timeout@sandbox-flaky-places"]


def test_tc08_missing_failure_record_fails_the_case(settings: Settings) -> None:
    """Proposal TC-08 (p4): without a record of the failure the case is not recovered."""
    plan, log, *_ = _sandbox_plan(settings, "Hong Kong", fault="source_timeout")
    plan.data_quality.notes = [note for note in plan.data_quality.notes if "fallback" not in note]
    case = EvalCase(id="x", dataset="hongkong", input="", fault="source_timeout")
    score = score_plan(case, plan=plan, initial=plan, log=log, start=plan.dates.start)
    assert score.disruption["outcomes"]["failure_detected"] is True
    assert score.disruption["outcomes"]["failure_recorded"] is False
    assert score.disruption["handled"] is False
    assert score.failure_class() == "source_timeout_not_handled"

    closure = EvalCase(id="y", dataset="hongkong", input="", fault="venue_closure", fault_detail={"venue": "Peak Tram"})
    outcome = disruption_outcomes(
        closure, initial=plan, plan=plan, log=log, start=plan.dates.start, other_constraints_hold=True
    )
    assert not outcome["outcomes"]["invalid_item_removed"] and not outcome["outcomes"]["failure_recorded"]


# --- TC-09: grounding against the tool log ----------------------------------


def test_tc09_every_planned_item_matches_the_tool_log(trials: dict) -> None:
    """Proposal TC-09 (p4): each venue, stay, fare and forecast traces to a tool result."""
    for name in PLANNING_SETS:
        for case in trials["runs"][name]["cases"]:
            ground = case["plan_checks"]["grounding"]
            assert ground["items"] > 0 and ground["ungrounded"] == [], (case["case_id"], ground)
            assert case["scores"]["grounding"] == 1.0


def test_tc09_items_without_a_matching_tool_result_are_ungrounded(settings: Settings) -> None:
    """Proposal TC-09 (p4): an item whose source is not in this run's tool log is ungrounded."""
    plan, log, *_ = _sandbox_plan(settings, "Kyoto", days=2)
    assert grounding(plan, log)["score"] == 1.0
    day = plan.days[0]
    real = day.items[0]
    ref = SourceRef(title="Invented", url="https://example.com/invented-teahouse", source="model")
    day.items.append(PlanItem(type="attraction", name="Invented Teahouse", start="21:00", end="22:00", source_refs=[ref]))
    real.source_refs = [ref]  # a real name with a source the tools never returned
    plan.hotels[0].price_per_night.amount += 50  # a price the hotel source did not quote
    plan.days[1].weather.high_c = 35  # a forecast the weather source did not give
    result = grounding(plan, log)
    assert set(result["ungrounded"]) >= {
        "attraction: Invented Teahouse",
        f"{real.type.value}: {real.name}",
        f"hotel: {plan.hotels[0].name}",
        f"weather: {plan.days[1].date.isoformat()}",
    }
    assert result["score"] < 1.0
    case = EvalCase(id="x", dataset="japan", input="", days=2)
    assert score_plan(case, plan=plan, initial=plan, log=log, start=plan.dates.start).failure_class() == "ungrounded_items"


# --- TC-09: transit times and opening hours ---------------------------------


def _legs(plan) -> list[tuple[int, PlanItem, PlanItem]]:
    """(day index, previous stop, stop) for every consecutive pair that has coordinates."""
    out = []
    for index, day in enumerate(plan.days):
        ordered = sorted(day.items, key=lambda item: item.start)
        out += [(index, a, b) for a, b in zip(ordered, ordered[1:]) if a.geo is not None and b.geo is not None]
    return out


def test_tc09_transfer_times_come_from_a_logged_routes_call(settings: Settings) -> None:
    """Proposal TC-09 (p4): every transit time on the plan is a routes result in this trip's tool log."""
    plan, log, *_ = _sandbox_plan(settings, "Hong Kong", days=2)
    calls = [entry for entry in log.entries if entry["tool"] == "routes.segments"]
    assert len(calls) == len(plan.days) and all(entry["status"] == "ok" for entry in calls)
    logged = {fact["id"]: fact for entry in calls for fact in entry["facts"]}
    pairs = _legs(plan)
    assert pairs
    for _, previous, item in pairs:
        transfer = item.transfer
        assert transfer is not None and transfer.from_item_id == previous.id
        assert (transfer.tool, transfer.source) == ("routes.segments", "transport-model")
        fact = logged[transfer.result_id]
        assert (fact["minutes"], fact["mode"]) == (transfer.minutes, transfer.mode)
        if item.type.value == "attraction":  # the stop's text quotes the same leg
            assert f"About {transfer.minutes} min" in item.notes
    legs = grounding(plan, log)["by_kind"]["transfer"]
    assert legs == {"checked": len(pairs), "grounded": len(pairs), "ungrounded": []}


def test_tc09_ungrounded_transfer_times_are_caught(settings: Settings) -> None:
    """Proposal TC-09 (p4): a transit time the routes tool did not return for those two stops is ungrounded."""
    plan, log, *_ = _sandbox_plan(settings, "Hong Kong", days=2)
    pairs = _legs(plan)
    assert len(pairs) >= 4
    (d1, a1, b1), (d2, a2, b2), (d3, a3, b3), (d4, a4, b4) = pairs[:4]
    b1.transfer.minutes += 7  # a time no tool returned
    b2.transfer.result_id = "invented"  # a leg id that is not in the log
    b3.transfer = None  # a transfer the planner never looked up
    b4.transfer = b1.transfer.model_copy(update={"minutes": b1.transfer.minutes - 7})  # a real leg, other stops
    result = grounding(plan, log)
    flagged = {f"transfer: day {d + 1} {a.name} -> {b.name}" for d, a, b in ((d1, a1, b1), (d2, a2, b2), (d3, a3, b3), (d4, a4, b4))}
    assert set(result["by_kind"]["transfer"]["ungrounded"]) == flagged
    assert flagged <= set(result["ungrounded"])
    assert result["by_kind"]["transfer"]["grounded"] == len(pairs) - 4
    assert result["score"] < 1.0
    case = EvalCase(id="x", dataset="hongkong", input="", days=2)
    assert score_plan(case, plan=plan, initial=plan, log=log, start=plan.dates.start).failure_class() == "ungrounded_items"


def test_tc09_a_reported_mtr_delay_on_a_transfer_is_grounded_in_the_status_result(settings: Settings) -> None:
    """Proposal TC-09 (p4): leg minutes come from routes.segments, the delay from routes.status."""
    detail = {"day": 1, "line": "Island Line", "delay_minutes": 30}
    plan, log, *_ = _sandbox_plan(settings, "Hong Kong", days=2, fault="transit_delay", detail=detail)
    delayed = [item for _, item in plan.iter_items() if item.transfer and item.transfer.delay_minutes]
    assert delayed, "the delay day has metro transfers"
    for item in delayed:
        assert (item.transfer.delay_tool, item.transfer.mode) == ("routes.status", "metro")
        assert item.transfer.delay_minutes <= 30
    assert short_transfers(plan, delay_dates={plan.days[0].date.isoformat()}, delay=30) == []
    assert grounding(plan, log)["by_kind"]["transfer"]["ungrounded"] == []
    tampered = delayed[0]
    tampered.transfer.delay_minutes += 5  # longer than the status source said
    flagged = grounding(plan, log)["by_kind"]["transfer"]["ungrounded"]
    assert len(flagged) == 1 and flagged[0].endswith(f"-> {tampered.name}")


def test_tc09_opening_hours_are_carried_and_scheduled_within(settings: Settings) -> None:
    """Proposal TC-09 (p4): stated hours reach the stop and bound its time; no hours means "hours unknown"."""
    plan, log, *_ = _sandbox_plan(settings, "Hong Kong", days=2)
    attractions = [item for _, item in plan.iter_items() if item.type.value == "attraction"]
    meals = [item for _, item in plan.iter_items() if item.type.value == "meal"]
    assert attractions and meals
    for item in attractions:  # the sandbox place source states 09:00-17:00
        assert (item.hours_status, item.opening_hours) == ("stated", ["09:00-17:00"])
        assert "09:00" <= item.start and item.end <= "17:00", item
    for item in meals:  # the sandbox restaurants state no hours
        assert (item.hours_status, item.opening_hours) == ("unknown", [])
    result = grounding(plan, log)
    hours = result["by_kind"]["opening_hours"]
    assert hours == {"checked": len(attractions), "grounded": len(attractions), "ungrounded": []}
    assert result["hours_unknown"] == len(meals)


def test_tc09_ungrounded_opening_hours_are_caught(settings: Settings) -> None:
    """Proposal TC-09 (p4): hours the place source did not state, for that stop, are ungrounded."""
    plan, log, *_ = _sandbox_plan(settings, "Hong Kong", days=2)
    attraction = next(item for _, item in plan.iter_items() if item.type.value == "attraction")
    meal = next(item for _, item in plan.iter_items() if item.type.value == "meal")
    attraction.opening_hours = ["09:00-21:00"]  # longer than the source said
    meal.opening_hours, meal.hours_status = ["11:00-22:00"], "stated"  # the source gave none
    result = grounding(plan, log)
    assert set(result["by_kind"]["opening_hours"]["ungrounded"]) == {f"hours: {attraction.name}", f"hours: {meal.name}"}
    assert result["score"] < 1.0


def test_tc09_eval_run_reports_transfer_and_hours_grounding(trials: dict) -> None:
    """Proposal TC-09 (p4): POST /api/eval/trials reports transit-time and opening-hours grounding."""
    for name in PLANNING_SETS:
        metrics = _metrics(trials, name)
        legs, hours = metrics["grounding_transfers"], metrics["grounding_hours"]
        assert legs["value"] == 100.0 and legs["label"].startswith("Transfer times grounded (")
        assert hours["value"] == 100.0 and hours["label"].startswith("Opening hours grounded (")
        unknown = 0
        for case in trials["runs"][name]["cases"]:
            ground = case["plan_checks"]["grounding"]
            assert ground["by_kind"]["transfer"]["checked"] > 0, case["case_id"]
            assert ground["by_kind"]["opening_hours"]["checked"] > 0, case["case_id"]
            assert ground["by_kind"]["transfer"]["ungrounded"] == ground["by_kind"]["opening_hours"]["ungrounded"] == []
            unknown += ground["hours_unknown"]
        assert metrics["hours_unknown"]["value"] == unknown > 0  # the sandbox restaurants state no hours
        assert "| Transfers | Opening hours |" in trials["exports"][name]


def test_tc09_opening_hours_parser_reads_google_osm_and_plain_formats() -> None:
    """Proposal TC-09 (p4): hours are read from each source's own format, never guessed."""
    from app.application.opening_hours import lines_for, windows_on

    monday, tuesday, wednesday, thursday, friday, saturday = (date(2027, 4, 5) + timedelta(days=i) for i in range(6))
    google = [
        "Monday: 9:00 AM – 5:00 PM",
        "Tuesday: Closed",
        "Wednesday: Open 24 hours",
        "Thursday: 11:30 AM – 2:30 PM, 5:30 – 10:00 PM",
        "Friday: 6:00 PM – 2:00 AM",
    ]
    assert windows_on(google, monday) == [(540, 1020)]
    assert windows_on(google, tuesday) == []
    assert windows_on(google, wednesday) == [(0, 1440)]
    assert windows_on(google, thursday) == [(690, 870), (1050, 1320)]
    assert windows_on(google, friday) == [(1080, 1440)]
    assert windows_on(google, saturday) is None  # no line for Saturday
    assert lines_for(google, thursday) == [google[3]]
    osm = ["Mo-Fr 09:00-17:00; Sa 10:00-14:00; Su off"]
    assert windows_on(osm, friday) == [(540, 1020)]
    assert windows_on(osm, saturday) == [(600, 840)]
    assert windows_on(osm, saturday + timedelta(days=1)) == []
    assert windows_on(["Tu-Su 10:00-18:00; Mo off"], monday) == []
    assert windows_on(["24/7"], monday) == [(0, 1440)]
    assert windows_on(["09:00-17:00"], monday) == [(540, 1020)]
    assert windows_on(["9AM-5PM daily"], monday) == [(540, 1020)]
    assert windows_on(["10:00-18:00, closed Mondays"], monday) is None  # stated, but not readable
    assert windows_on(["sunrise-sunset"], monday) is None
    assert windows_on([], monday) is None


def test_tc09_stops_are_scheduled_inside_their_stated_hours() -> None:
    """Proposal TC-09 (p4): a stop waits for its opening, ends by its closing, and a closed stop is left out."""
    from app.application.planner import _schedule
    from app.eval_checks import transit_minutes

    def row(place_id: str, name: str, lat: float, lng: float, hours: list[str] | None = None) -> dict:
        return {
            "id": place_id, "name": name, "geo": {"lat": lat, "lng": lng}, "opening_hours": hours or [],
            "source_refs": [{"title": name, "url": f"https://example.com/{place_id}", "source": "test"}],
        }

    saturday = date(2027, 4, 10)
    places = [
        row("a", "Harbour Walk", 22.300, 114.170),
        row("b", "Night Market", 22.306, 114.170, ["Mo-Su 14:00-23:00"]),
        row("c", "Morning Museum", 22.302, 114.172, ["Mo-Su 09:00-12:00"]),
        row("d", "Weekday Gallery", 22.301, 114.168, ["Mo-Fr 09:00-17:00; Sa,Su off"]),
    ]
    lunch = row("m1", "Noodle Bar", 22.303, 114.171, ["Mo-Su 11:00-15:00"])
    dinner = row("m2", "Dim Sum Hall", 22.304, 114.169)
    items, left_out = _schedule(saturday, places, lunch, dinner, pace="balanced")
    by_name = {item.name: item for item in items}
    assert left_out == ["Weekday Gallery"]
    assert by_name["Night Market"].start >= "14:00"
    assert by_name["Morning Museum"].end <= "12:00"
    assert "11:00" <= by_name["Noodle Bar"].start and by_name["Noodle Bar"].end <= "15:00"
    assert by_name["Dim Sum Hall"].hours_status == "unknown"
    for previous, item in zip(items, items[1:]):
        gap = int(item.start[:2]) * 60 + int(item.start[3:]) - (int(previous.end[:2]) * 60 + int(previous.end[3:]))
        assert gap >= transit_minutes(previous.geo, item.geo), (previous.name, item.name)


def test_tc09_osm_opening_hours_reach_the_place_candidate(settings: Settings, monkeypatch) -> None:
    """Proposal TC-09 (p4): the OpenStreetMap source passes its opening_hours tag on, verbatim."""
    from app.infrastructure.providers import places as module

    async def fake_request(*_, **__):  # request_json wraps a JSON list as {"data": [...]}
        return {"data": [
            {"name": "Tai Kwun", "lat": "22.2814", "lon": "114.1543", "osm_type": "node", "osm_id": 7,
             "extratags": {"opening_hours": "Mo-Su 10:00-23:00", "website": "https://www.taikwun.hk"}},
            {"name": "Unlisted Lane", "lat": "22.2800", "lon": "114.1500", "osm_type": "node", "osm_id": 8, "extratags": {}},
        ]}

    monkeypatch.setattr(module, "request_json", fake_request)
    rows = asyncio.run(
        module.NominatimPlacesSource(settings).search(destination="Hong Kong", geo=None, category="attraction", limit=5)
    )
    assert [row.opening_hours for row in rows] == [["Mo-Su 10:00-23:00"], []]


def test_tc09_an_edit_refreshes_the_transfers_it_changes(settings: Settings) -> None:
    """Proposal TC-09 (p4): after a stop is removed, the next stop's transit time is fetched again and logged."""
    plan, log, _registry, plan_path, gateway, context, _ = _sandbox_plan(settings, "Hong Kong", days=2)
    day = plan.days[0]
    ordered = sorted(day.items, key=lambda item: item.start)
    middle = next(
        item for index, item in enumerate(ordered[1:-1], start=1)
        if item.type.value == "attraction" and ordered[index + 1].geo is not None
    )
    following = ordered[ordered.index(middle) + 1]
    log.phase = "edit"
    updated, _ = asyncio.run(
        ModifyPath(planner=plan_path.planner, plan_path=plan_path, settings=settings, gateway=gateway).run(
            plan=plan,
            context=context,
            message=f"Remove {middle.name} from the plan.",
            route_parts=["attraction"],
            toolbox=RecordingToolbox(log, path="modify"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
        )
    )
    assert middle.name not in {item.name for _, item in updated.iter_items()}
    moved = updated.find_item(following.id)[1]
    assert moved.transfer.from_item_id != middle.id
    assert [entry["status"] for entry in log.entries if entry["phase"] == "edit" and entry["tool"] == "routes.segments"] == ["ok"]
    assert grounding(updated, log)["by_kind"]["transfer"]["ungrounded"] == []
    assert short_transfers(updated) == []
