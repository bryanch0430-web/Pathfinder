"""Deterministic evaluation harness.

The harness runs the real application pipeline against a sandbox provider
registry. No network and no model key is needed, so a scored run is repeatable.
The sandbox is labelled as such in the UI and in the report.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.application.llm_gateway import LLMGateway
from app.application.modification import ModifyPath
from app.application.planning import PlanPath
from app.application.planner import Planner
from app.application.prompts import PromptLibrary
from app.application.router import RuleRouter
from app.application.toolbox import Toolbox
from app.application.validation import validate_plan
from app.config import Settings
from app.domain.models import (
    AgentStatus,
    ConstraintStatus,
    DataStatus,
    DatasetMeta,
    EvalCase,
    EvalCaseResult,
    EvalMetric,
    EvaluationRun,
    FlightCandidate,
    GeoLocation,
    GeoPoint,
    HotelCandidate,
    Intent,
    Money,
    NoteItem,
    PlaceCandidate,
    PriceKind,
    RouteLeg,
    SourceRef,
    TripContext,
    utcnow,
    WeatherDay,
)
from app.eval_checks import (
    RecordingToolbox,
    ToolLog,
    judge_family_check,
    judge_metric,
    planning_metrics,
    render_markdown,
    score_plan,
)
from app.eval_metering import RunLedger
from app.infrastructure.llm import NullLLM
from app.infrastructure.judge import Judge
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers.fx import FxService
from app.infrastructure.store import SQLiteStore
from app.security import canary_leaked, screen_message

FIXTURES = Path(__file__).resolve().parents[1] / "eval" / "fixtures"
DATASETS = {
    "routing": "routing.jsonl",
    "injection": "injection.jsonl",
    "hongkong": "hongkong.json",
    "japan": "japan.json",
    "travelplanner": "travelplanner.json",
    "efficiency": "efficiency.json",
    "edit_vs_replan": "edit_vs_replan.json",
}
# Paired cost comparisons (Proposal TC-10, TC-11), run by app.eval_efficiency.
COST_DATASETS = frozenset({"efficiency", "edit_vs_replan"})


class EvalService:
    def __init__(self, store: SQLiteStore, settings: Settings):
        self.store = store
        self.settings = settings
        self.judge = Judge(settings)
        self.judge_check = judge_family_check(settings, judge_enabled=self.judge.available)
        self.seed_defaults()

    def seed_defaults(self) -> None:
        existing = {dataset.id for dataset in self.store.list_eval_datasets()}
        for name in DATASETS:
            cases = self.load_cases(name)
            if name in existing:
                for dataset in self.store.list_eval_datasets():
                    if dataset.id == name and dataset.item_count != len(cases):
                        dataset.item_count = len(cases)
                        self.store.save_eval_dataset(dataset)
                continue
            self.store.save_eval_dataset(
                DatasetMeta(
                    id=name,
                    name=name.replace("_", " ").title(),
                    version="1.0",
                    split="dev",
                    frozen=True,
                    item_count=len(cases),
                )
            )

    def load_cases(self, name: str) -> list[EvalCase]:
        filename = DATASETS.get(name)
        if not filename:
            return []
        path = FIXTURES / filename
        if not path.exists():
            return []
        if path.suffix == ".jsonl":
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        else:
            rows = json.loads(path.read_text(encoding="utf-8"))
        cases: list[EvalCase] = []
        for row in rows:
            row.setdefault("dataset", name)
            if name == "routing":
                row.setdefault("kind", "routing")
            elif name == "injection":
                row.setdefault("kind", "injection")
            else:
                row.setdefault("kind", "planning")
            cases.append(EvalCase.model_validate(row))
        return cases

    async def run(
        self,
        *,
        dataset_id: str,
        experiment_tag: str,
        split: str = "all",
        include_heldout: bool = True,
    ) -> EvaluationRun:
        meta = self.store.get_eval_dataset(dataset_id)
        if meta is None:
            raise KeyError(dataset_id)
        if not meta.frozen:
            raise PermissionError("dataset is not frozen; freeze it before a scored run")
        cases = self.load_cases(meta.id)
        if split != "all":
            cases = [case for case in cases if case.split == split]
        elif not include_heldout:
            cases = [case for case in cases if case.split == "dev"]
        started = time.perf_counter()
        results: list[EvalCaseResult] = []
        latencies: list[int] = []
        # Per-run account of provider calls, logged tool spans and writer tokens.
        ledger = RunLedger()
        judge_tokens = self.judge.tokens
        suite_metrics: list[EvalMetric] = []
        snapshot: dict = {}

        try:
            if meta.id == "routing":
                for case in cases:
                    results.append(self._routing_case(case))
            elif meta.id == "injection":
                results.extend(await self._injection_cases(cases))
            elif meta.id in COST_DATASETS:
                from app.eval_efficiency import run_cost_suite

                report = await run_cost_suite(meta.id, cases, self.settings, ledger)
                results, suite_metrics, snapshot = report.results, report.metrics, report.snapshot
            else:
                for case in cases:
                    case_started = time.perf_counter()
                    result = await self._planning_case(case, ledger=ledger)
                    results.append(result)
                    latencies.append(int((time.perf_counter() - case_started) * 1000))
            if ledger.made_total:
                suite_metrics.append(ledger.coverage_metric())
        finally:
            ledger.close()
        ledger.tokens += self.judge.tokens - judge_tokens

        metrics = self._metrics(meta.id, results, latencies) + suite_metrics
        previous = self._previous_run(meta.id)
        if previous:
            baseline = {metric.key: metric.value for metric in previous.metrics}
            for metric in metrics:
                if metric.key in baseline:
                    metric.baseline = baseline[metric.key]
                    metric.delta = round(metric.value - baseline[metric.key], 2)
                    metric.direction = "up" if metric.delta > 0 else "down" if metric.delta < 0 else "flat"
        run = EvaluationRun(
            experiment_tag=experiment_tag,
            dataset_id=meta.id,
            dataset_version=meta.version,
            status="completed",
            metrics=metrics,
            cases=results,
            model_snapshot={
                "writer": "sandbox-offline",
                "router": "rules",
                "judge": f"llm:{self.judge.model}" if self.judge.available else "deterministic-scripts",
                "judge_check": self.judge_check,
                "settings": {
                    "router_threshold": self.settings.router_threshold,
                    "preplan_budget_seconds": self.settings.preplan_budget_seconds,
                    "max_repairs": self.settings.max_repairs,
                },
                **snapshot,
            },
            token_usage=ledger.tokens,
            latency_ms=int((time.perf_counter() - started) * 1000),
            finished_at=utcnow(),
        )
        self.store.save_eval_run(run)
        return run

    # -- case evaluators --------------------------------------------------

    def _routing_case(self, case: EvalCase) -> EvalCaseResult:
        context = TripContext(
            session_id="eval",
            destination=case.destination or "Kyoto",
            start_date=date.today() + timedelta(days=30),
            end_date=date.today() + timedelta(days=32),
            party_size=case.party_size or 2,
            budget=case.budget or 3000,
            currency=case.currency,
        )
        decision = RuleRouter().route(message=case.input, context=context, plan_summary={"trip_id": "x"}, history=[])
        passed = decision.intent == case.expected_intent
        return EvalCaseResult(
            case_id=case.id,
            dataset=case.dataset,
            split=case.split,
            passed=passed,
            score=1.0 if passed else 0.0,
            failure_class="" if passed else "wrong_intent",
            input=case.input,
            output_summary=f"{decision.intent.value} ({decision.confidence:.2f})",
            judge_reason="Expected " + (case.expected_intent.value if case.expected_intent else "unknown"),
            scores={"routing_accuracy": 1.0 if passed else 0.0},
        )

    async def _injection_cases(self, cases: list[EvalCase]) -> list[EvalCaseResult]:
        """Proposal TC-05 (p4): each probe is a real turn, contained only if the route stays
        typed, the agent returns its own output, only allowlisted tools run and the canary is
        absent. See app/injection_probes.py."""
        from app.injection_probes import score_probes

        return await score_probes(cases, self.settings)

    async def _planning_case(self, case: EvalCase, ledger: RunLedger | None = None) -> EvalCaseResult:
        settings = self.settings.model_copy(
            update={"preplan_budget_seconds": 20, "agent_timeout_seconds": 10, "enable_flights": True}
        )
        start = date.today() + timedelta(days=30)
        registry = SandboxRegistry(
            settings, case.destination or "Kyoto", fault=case.fault, fault_detail=case.fault_detail, start=start
        )
        if ledger is not None:
            ledger.instrument(registry)  # Proposal TC-12: count every provider call
        gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
        planner = Planner(gateway, settings, registry.fx)
        plan_path = PlanPath(planner=planner, registry=registry, settings=settings)
        end = start + timedelta(days=max((case.days or 3) - 1, 0))
        context = TripContext(
            session_id="eval",
            destination=case.destination or "Kyoto",
            origin=case.origin or "Hong Kong",
            start_date=start,
            end_date=end,
            days=case.days or 3,
            party_size=case.party_size or 1,
            budget=case.budget or 3000,
            currency=case.currency,
            preferences=[],
            hard_constraints=case.constraints,
        )
        # The app's Toolbox, logging into this run's tool log (Proposal TC-09, p4).
        log = ToolLog()
        toolbox = RecordingToolbox(log, path="plan", request_id="eval")
        if ledger is not None:
            ledger.track(log)
        errors: list[str] = []
        plan = None
        try:
            plan = await plan_path.run(
                context=context,
                session_id="eval",
                toolbox=toolbox,
                tracer=NoopTracer(),
                emit=lambda *_: None,
                version=1,
            )
        except Exception as exc:
            errors.append(str(exc)[:200])

        passed = plan is not None
        failure_class = "" if passed else "plan_failed"
        checks: list[str] = []
        scores: dict[str, float] = {
            "constraint_satisfaction": 0.0,
            "grounding": 0.0,
            "edit_efficiency": 0.0,
        }
        if case.fault:
            scores["error_recovery"] = 0.0
        plan_checks: dict = {}
        summary = ""
        if plan:
            initial = plan
            edit_ok = await self._edit_check(
                plan, context, settings, registry, gateway, plan_path,
                tool_log=ledger.store if ledger is not None else None,
            )
            if case.fault == "venue_closure":
                plan = await self._reroute_closure(case, plan, context, settings, registry, gateway, plan_path, log)
            checks = validate_plan(plan, settings)
            if checks:
                passed = False
                failure_class = "invalid_plan"
            # Proposal TC-07/08/09 (p4): met/total constraints, recovery, grounding.
            report = score_plan(case, plan=plan, initial=initial, log=log, start=start)
            plan_checks = report.as_dict()
            summary = report.summary()
            if passed and report.failure_class():
                passed = False
                failure_class = report.failure_class()
            scores["constraint_satisfaction"] = report.satisfaction
            scores["grounding"] = report.grounding["score"]
            scores["edit_efficiency"] = 1.0 if edit_ok else 0.0
            if case.fault:
                scores["error_recovery"] = 1.0 if report.disruption and report.disruption["handled"] else 0.0
            if not edit_ok:
                checks.append("edit path did not preserve unaffected items")
        if ledger is not None:
            ledger.tokens += gateway.prompt_tokens + gateway.completion_tokens

        verdict = None
        if plan is not None:
            verdict = await self.judge.score(case=case, plan=plan, checks=checks, scores=scores)
        flags = list(verdict.flags) if verdict else []
        if verdict and self.judge_check["same_family"]:
            flags.append("judge shares the writer's model family")
        deterministic = "; ".join([summary, *checks[:3]]).strip("; ") or "; ".join(errors)

        return EvalCaseResult(
            case_id=case.id,
            dataset=case.dataset,
            split=case.split,
            passed=passed,
            score=1.0 if passed else 0.0,
            failure_class=failure_class,
            input=case.input,
            output_summary=(plan.summary[:240] if plan else "; ".join(errors)),
            judge_reason=(
                f"{deterministic} Judge: {verdict.reason}" if verdict and verdict.reason else deterministic
            ),
            judge_score=verdict.score if verdict else None,
            judge_flags=flags,
            tool_summary=[
                f"{gap.section}:{gap.kind.value}" for gap in (plan.data_quality.gaps if plan else [])[:6]
            ],
            scores=scores,
            plan_checks=plan_checks,
        )

    async def _reroute_closure(self, case, plan, context, settings, registry, gateway, plan_path, log):
        """Proposal TC-08 (p4): close a planned venue and send the report down the edit path.

        The sandbox place source stops listing the venue, and the traveller's report
        is routed like any chat message. The returned plan is what gets scored.
        """
        venue = str(case.fault_detail.get("venue", ""))
        message = str(case.fault_detail.get("report") or f"{venue} is closed today. Replace {venue} in the plan.")
        registry.close_venue(venue)
        log.phase = "disruption"
        decision = RuleRouter().route(message=message, context=context, plan_summary=plan.summary_for_prompt(), history=[])
        if decision.intent != Intent.MODIFY:
            return plan
        try:
            modify = ModifyPath(planner=plan_path.planner, plan_path=plan_path, settings=settings, gateway=gateway)
            updated, _ = await modify.run(
                plan=plan,
                context=context,
                message=message,
                route_parts=decision.affected_parts,
                toolbox=RecordingToolbox(log, path="modify", request_id="eval"),
                tracer=NoopTracer(),
                emit=lambda *_: None,
            )
            return updated
        except Exception:
            return plan

    async def _edit_check(self, plan, context, settings, registry, gateway, plan_path, tool_log=None) -> bool:
        try:
            modify = ModifyPath(planner=plan_path.planner, plan_path=plan_path, settings=settings, gateway=gateway)
            updated, diff = await modify.run(
                plan=plan,
                context=context,
                message="Swap the hotel for the other option, keep everything else.",
                route_parts=["hotel"],
                toolbox=Toolbox(path="modify", tracer=NoopTracer(), store=tool_log, request_id="eval"),
                tracer=NoopTracer(),
                emit=lambda *_: None,
            )
            old_names = {item.name for day in plan.days for item in day.items}
            new_names = {item.name for day in updated.days for item in day.items}
            return updated.version == plan.version + 1 and old_names == new_names
        except Exception:
            return False

    # -- metrics ----------------------------------------------------------

    def _metrics(self, dataset: str, results: list[EvalCaseResult], latencies: list[int]) -> list[EvalMetric]:
        def pct(passed: int, total: int) -> float:
            return round(100.0 * passed / total, 1) if total else 0.0

        metrics: list[EvalMetric] = []
        if dataset == "routing":
            metrics.append(
                EvalMetric(
                    key="routing_accuracy",
                    label="Routing accuracy",
                    value=pct(sum(result.passed for result in results), len(results)),
                )
            )
            from app.routing_report import routing_breakdown  # Proposal TC-04 / TC-06

            metrics.extend(routing_breakdown(results))
        elif dataset == "injection":
            metrics.append(
                EvalMetric(
                    key="injection_containment",
                    label="Injection containment",
                    value=pct(sum(result.passed for result in results), len(results)),
                )
            )
            from app.injection_probes import injection_breakdown  # Proposal TC-05 / TC-06

            metrics.extend(injection_breakdown(results))
        else:
            metrics.extend(planning_metrics(results))
            from app.dataset_splits import FIXTURE_FILES, split_metrics  # Proposal TC-06

            if dataset in FIXTURE_FILES:
                metrics.extend(split_metrics(dataset))
            if self.judge.available:
                metrics.append(judge_metric(self.judge_check))
            if latencies:
                metrics.append(
                    EvalMetric(
                        key="latency",
                        label="Mean latency",
                        value=round(sum(latencies) / len(latencies), 1),
                        unit="ms",
                    )
                )
        return metrics

    def _previous_run(self, dataset_id: str) -> EvaluationRun | None:
        runs = [run for run in self.store.list_eval_runs(limit=50) if run.dataset_id == dataset_id]
        return runs[1] if len(runs) > 1 else (runs[0] if runs else None)


# ---------------------------------------------------------------------------
# Sandbox: deterministic providers that implement the same ports as live data
# ---------------------------------------------------------------------------


class SandboxRegistry:
    """Sandbox providers, with one simulation per Hong Kong disruption (Proposal TC-03, p4).

    - ``source_timeout``: the first place source times out once; a fallback answers.
    - ``typhoon``: the weather source reports a typhoon signal on ``fault_detail["day"]``.
    - ``venue_closure``: ``close_venue()`` makes the place source stop listing a venue.
    - ``transit_delay``: the routes source adds ``delay_minutes`` to every transit leg.
    """

    def __init__(
        self,
        settings: Settings,
        destination: str,
        *,
        flaky: bool = False,
        fault: str = "",
        fault_detail: dict | None = None,
        start: date | None = None,
    ):
        self.settings = settings
        self.destination = destination
        detail = fault_detail or {}
        self.fault = fault or ("source_timeout" if flaky else "")
        self.flaky = self.fault == "source_timeout"
        self.geocoder = SandboxGeocoder(destination)
        places = SandboxPlaces(destination)
        self._places = places
        self.places = [FlakyPlaceSource(destination)] if self.flaky else []
        self.places.append(places)
        alerts: dict[date, str] = {}
        if self.fault == "typhoon":
            day = (start or date.today()) + timedelta(days=int(detail.get("day", 1)) - 1)
            alerts[day] = str(detail.get("signal") or "Typhoon Signal No. 8")
        self.weather = [SandboxWeather(alerts)]
        self.hotels = [SandboxHotels(destination)]
        self.flights = [SandboxFlights()]
        self.notes = [SandboxNotes()]
        delay = int(detail.get("delay_minutes", 0)) if self.fault == "transit_delay" else 0
        self.routes = [SandboxRoutes(delay_minutes=delay, line=str(detail.get("line", "")))]
        self.fx = SandboxFx()

    def close_venue(self, name: str) -> None:
        self._places.closed.add(name.lower())


BASE = {"Kyoto": (35.0116, 135.7681), "Tokyo": (35.6762, 139.6503), "Osaka": (34.6937, 135.5023),
        "Hong Kong": (22.3193, 114.1694), "New York": (40.7128, -74.0060), "San Francisco": (37.7749, -122.4194)}

# (name, lat, lng, Google-style place types). Rows are listed best-rated first.
PLACES = {
    "Kyoto": [("Kiyomizu-dera", 34.9949, 135.7850, ["place_of_worship"]),
              ("Fushimi Inari Taisha", 34.9671, 135.7727, ["place_of_worship"]),
              ("Arashiyama Bamboo Grove", 35.0094, 135.6668, ["park"]),
              ("Gion", 35.0037, 135.7752, ["tourist_attraction"])],
    "Tokyo": [("Senso-ji", 35.7148, 139.7967, ["place_of_worship"]),
              ("Meiji Jingu", 35.6764, 139.6993, ["place_of_worship"]),
              ("Shibuya Sky", 35.6580, 139.7016, ["observation_deck"]),
              ("teamLab Planets", 35.6490, 139.7900, ["museum", "art_gallery"]),
              ("Tokyo National Museum", 35.7188, 139.7765, ["museum"]),
              ("Shinjuku Gyoen", 35.6852, 139.7101, ["park"]),
              ("Tsukiji Outer Market", 35.6654, 139.7707, ["market"]),
              ("Tokyo Skytree", 35.7101, 139.8107, ["observation_deck"]),
              ("Ueno Park", 35.7156, 139.7745, ["park"]),
              ("Ghibli Museum", 35.6962, 139.5704, ["museum"])],
    "Osaka": [("Osaka Castle", 34.6873, 135.5259, ["historical_landmark"]),
              ("Dotonbori", 34.6687, 135.5013, ["tourist_attraction"]),
              ("Umeda Sky Building", 34.7055, 135.4902, ["observation_deck"]),
              ("Sumiyoshi Taisha", 34.6123, 135.4930, ["place_of_worship"]),
              ("Osaka Aquarium Kaiyukan", 34.6545, 135.4290, ["aquarium"]),
              ("Kuromon Market", 34.6654, 135.5066, ["market"])],
    "Hong Kong": [("Victoria Peak", 22.2759, 114.1455, ["natural_feature"]),
                  ("Peak Tram", 22.2779, 114.1597, ["transit_station"]),
                  ("Star Ferry", 22.2940, 114.1685, ["ferry_terminal"]),
                  ("Tian Tan Buddha", 22.2540, 113.9050, ["place_of_worship"]),
                  ("Temple Street Night Market", 22.3060, 114.1700, ["market"]),
                  ("Hong Kong Museum of History", 22.3017, 114.1772, ["museum"]),
                  ("M+", 22.3015, 114.1600, ["museum", "art_gallery"]),
                  ("Tai Kwun", 22.2814, 114.1543, ["museum", "art_gallery"]),
                  ("Ocean Park", 22.2467, 114.1757, ["amusement_park"]),
                  ("K11 Musea", 22.2946, 114.1747, ["shopping_mall"])],
    "New York": [("Metropolitan Museum of Art", 40.7794, -73.9632, ["museum"]),
                 ("Central Park", 40.7829, -73.9654, ["park"]),
                 ("The High Line", 40.7480, -74.0048, ["park"]),
                 ("Brooklyn Bridge", 40.7061, -73.9969, ["bridge"])],
    "San Francisco": [("Golden Gate Bridge", 37.8199, -122.4783, ["bridge"]),
                      ("Alcatraz Island", 37.8267, -122.4230, ["historical_landmark"]),
                      ("Ferry Building", 37.7955, -122.3937, ["market"]),
                      ("Golden Gate Park", 37.7694, -122.4862, ["park"])],
}

# (name, lat offset, lng offset, place types, description) around the city base.
_DEFAULT_RESTAURANTS = [
    ("{city} Kitchen", 0.003, 0.002, ["restaurant"], "Neighbourhood restaurant."),
    ("{city} River Cafe", -0.002, 0.004, ["cafe"], "Cafe by the water."),
]
RESTAURANTS = {
    "Osaka": [
        ("Paprika Shokudo Vegan", 0.004, -0.006, ["vegan_restaurant", "restaurant"], "Plant-based set meals."),
        ("Dotonbori Kushikatsu", -0.025, -0.002, ["japanese_restaurant", "restaurant"], "Deep-fried skewers of meat and seafood."),
        ("Osaka Vegetarian Kitchen", 0.002, 0.003, ["vegetarian_restaurant", "restaurant"], "Vegetarian teishoku and curry."),
        ("Namba Okonomiyaki", -0.024, 0.001, ["japanese_restaurant", "restaurant"], "Pork and squid okonomiyaki."),
        ("Shitennoji Shojin Ryori", -0.040, 0.014, ["vegetarian_restaurant", "japanese_restaurant"], "Buddhist temple cuisine."),
        ("Osaka River Cafe", -0.002, 0.004, ["cafe"], "Cafe by the water."),
    ],
    "New York": [
        ("New York Kitchen", 0.003, 0.002, ["american_restaurant", "restaurant"], "Burgers and steaks."),
        ("Dirt Candy", 0.004, 0.019, ["vegetarian_restaurant", "restaurant"], "Vegetable tasting menus."),
        ("New York River Cafe", -0.002, 0.004, ["cafe"], "Cafe by the water."),
        ("Superiority Burger", 0.016, 0.019, ["vegetarian_restaurant", "restaurant"], "Vegetarian burgers and sides."),
    ],
}


class SandboxGeocoder:
    name = "sandbox-geocoder"

    def __init__(self, destination: str):
        self.destination = destination

    async def geocode(self, name: str, *, country_hint: str = "") -> GeoLocation:
        lat, lng = BASE.get(self.destination, (35.0, 135.0))
        return GeoLocation(name=self.destination, country_code="XX", timezone="UTC", geo=GeoPoint(lat=lat, lng=lng), source=self.name)


class SandboxPlaces:
    name = "sandbox-places"

    def __init__(self, destination: str):
        self.destination = destination
        # Venues the source has stopped listing (a simulated closure).
        self.closed: set[str] = set()

    async def search(self, *, destination, geo, category, limit, query=None):
        lat, lng = BASE.get(self.destination, (35.0, 135.0))
        if category in {"restaurant", "meal", "food"}:
            rows = RESTAURANTS.get(self.destination, _DEFAULT_RESTAURANTS)
            return [
                PlaceCandidate(
                    name=name.format(city=self.destination),
                    category="Cafe" if "cafe" in types else "Restaurant",
                    description=description,
                    geo=GeoPoint(lat=lat + dlat, lng=lng + dlng),
                    address="Central",
                    source_refs=[_ref(name.format(city=self.destination))],
                    tags=types,
                )
                for name, dlat, dlng, types, description in rows[:limit]
            ]
        rows = [row for row in PLACES.get(self.destination, PLACES["Kyoto"]) if row[0].lower() not in self.closed]
        out = []
        for index, (name, plat, plng, types) in enumerate(rows[:limit]):
            out.append(
                PlaceCandidate(
                    name=name,
                    category="Attraction",
                    description=f"A verified stop in {self.destination}, suitable for a {60 + index * 15}-minute visit.",
                    geo=GeoPoint(lat=plat, lng=plng),
                    address=self.destination,
                    rating=4.6 - index * 0.1,
                    review_count=1000 - index * 100,
                    opening_hours=["09:00-17:00"],
                    source_refs=[_ref(name)],
                    tags=[*types, "sandbox"],
                )
            )
        return out


class FlakyPlaceSource(SandboxPlaces):
    """Times out on its first call, as a slow primary source would (``source_timeout``)."""

    name = "sandbox-flaky-places"
    _failed = False

    async def search(self, **kwargs):
        if not self._failed:
            self._failed = True
            from app.domain.errors import ProviderError
            from app.domain.models import FailureKind

            raise ProviderError("simulated place-source timeout", kind=FailureKind.TIMEOUT, source=self.name)
        return await super().search(**kwargs)


class SandboxWeather:
    """Partly cloudy every day, except for dates with a simulated alert (``typhoon``)."""

    name = "sandbox-weather"

    def __init__(self, alerts: dict[date, str] | None = None):
        self.alerts = alerts or {}

    async def forecast(self, geo, start, end):
        days = [date.fromordinal(start.toordinal() + i) for i in range((end - start).days + 1)]
        out = []
        for day in days:
            signal = self.alerts.get(day)
            if signal:
                out.append(
                    WeatherDay(
                        date=day, status=DataStatus.OK, summary=f"{signal} in force", high_c=27, low_c=24,
                        precipitation_probability=95, source=self.name,
                        note=f"Official warning: {signal}. Outdoor venues and ferry services are suspended.",
                    )
                )
            else:
                out.append(WeatherDay(date=day, status=DataStatus.OK, summary="Partly cloudy", high_c=22, low_c=14, precipitation_probability=20, source=self.name))
        return out


class SandboxHotels:
    name = "sandbox-hotels"

    def __init__(self, destination: str):
        self.destination = destination

    async def search(self, *, destination, check_in, check_out, party_size, budget, currency):
        lat, lng = BASE.get(self.destination, (35.0, 135.0))
        return [
            HotelCandidate(name=f"The {self.destination} Grand", area="Central", geo=GeoPoint(lat=lat + 0.01, lng=lng + 0.01), nightly_price=Money(amount=120, currency="USD"), price_kind=PriceKind.REFERENCE, rating=4.6, review_count=800, source_refs=[_ref(f"The {self.destination} Grand")], notes="Sandbox rate."),
            HotelCandidate(name=f"{self.destination} Garden Inn", area="Old town", geo=GeoPoint(lat=lat - 0.01, lng=lng - 0.01), nightly_price=Money(amount=90, currency="USD"), price_kind=PriceKind.REFERENCE, rating=4.3, review_count=500, source_refs=[_ref(f"{self.destination} Garden Inn")], notes="Sandbox rate."),
        ]


class SandboxFlights:
    name = "sandbox-flights"

    async def search(self, *, origin, destination, depart_date, return_date, party_size, currency):
        depart = datetime.combine(depart_date, datetime.min.time()).replace(hour=9)
        arrive = depart + timedelta(hours=4)
        return [
            FlightCandidate(airline="Pathfinder Air", flight_number="PF100", origin=origin[:3].upper() or "HKG", destination=destination[:3].upper(), depart_at=depart, arrive_at=arrive, duration_minutes=240, price=Money(amount=350, currency="USD"), price_kind=PriceKind.REFERENCE, booking_url="https://example.com/flight", source_refs=[_ref("Pathfinder Air PF100")])
        ]


class SandboxNotes:
    name = "sandbox-notes"

    async def search(self, query, *, limit=5, window_months=24):
        return [NoteItem(title=f"{query} guide", url="https://example.com/guide", source=self.name, snippet="Sandbox travel note.", published_at=utcnow() - timedelta(days=30))]


class SandboxRoutes:
    """Route estimates; with ``transit_delay`` every transit leg carries the delay."""

    name = "sandbox-routes"

    def __init__(self, *, delay_minutes: int = 0, line: str = ""):
        self.delay_minutes = delay_minutes
        self.line = line

    async def leg(self, origin, destination, *, mode="walk"):
        from app.infrastructure.providers.routes import estimate_leg

        leg = estimate_leg(origin, destination, mode)
        if self.delay_minutes and mode in {"transit", "metro"}:
            leg.duration_minutes = (leg.duration_minutes or 0) + self.delay_minutes
            leg.source = f"sandbox-routes:delay {self.line} +{self.delay_minutes} min".strip()
        return leg


class SandboxFx(FxService):
    """The static reference table only: a scored run must not depend on live rates."""

    async def refresh(self) -> None:
        self.source = "static"


def _ref(name: str) -> SourceRef:
    return SourceRef(title=name, url=f"https://example.com/{name.replace(' ', '-').lower()}", source="sandbox", fetched_at=utcnow())


def export_markdown(run: EvaluationRun) -> str:
    lines = [
        f"# Evaluation report — {run.experiment_tag}",
        "",
        f"- Dataset: `{run.dataset_id}` v{run.dataset_version}",
        f"- Run: `{run.id}`",
        f"- Created: {run.created_at.isoformat()}",
        f"- Model snapshot: `{json.dumps(run.model_snapshot)}`",
        "",
        "## Metrics",
        "",
        "| Metric | Value | Unit | Baseline | Delta |",
        "|---|---:|---|---:|---:|",
    ]
    for metric in run.metrics:
        lines.append(
            f"| {metric.label} | {metric.value} | {metric.unit} | "
            f"{metric.baseline if metric.baseline is not None else '—'} | "
            f"{metric.delta if metric.delta is not None else '—'} |"
        )
    lines += ["", "## Cases", "", "| Case | Split | Pass | Failure | Summary |", "|---|---|---:|---|---|"]
    failures = len(run.cases) - sum(1 for item in run.cases if item.passed)
    for case in run.cases:
        lines.append(
            f"| {case.case_id} | {case.split} | {'yes' if case.passed else 'no'} | {case.failure_class or '—'} | {case.output_summary[:120]} |"
        )
    passed = sum(1 for case in run.cases if case.passed)
    lines += ["", f"**{passed}/{len(run.cases)} cases passed.** {failures} failures."]
    lines += render_markdown(run.cases)
    return "\n".join(lines)
