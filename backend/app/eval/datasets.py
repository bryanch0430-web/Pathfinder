"""Evaluation datasets: the frozen proposal sets, plus the regional catalogue."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent.parent.parent / "eval" / "fixtures"

DATASETS: dict[str, dict[str, Any]] = {
    "routing": {
        "name": "Routing",
        "file": "routing.jsonl",
        "kind": "routing",
        "version": "2026-11-05",
        "description": "Intent accuracy on the frozen 84-utterance routing set.",
    },
    "injection": {
        "name": "Injection",
        "file": "injection.jsonl",
        "kind": "injection",
        "version": "2026-11-05",
        "description": "Prompt-injection containment: no canary or instruction leak.",
    },
    "hongkong": {
        "name": "Hong Kong",
        "file": "hongkong.json",
        "kind": "hongkong",
        "version": "2026-11-05",
        "description": "Disruption recovery and constraints across eight Hong Kong trips.",
    },
    "japan": {
        "name": "Japan",
        "file": "japan.json",
        "kind": "japan",
        "version": "2026-11-05",
        "description": "Sourced, grounded itineraries for six Japan trips.",
    },
    "travelplanner": {
        "name": "TravelPlanner-style",
        "file": "travelplanner.json",
        "kind": "travelplanner",
        "version": "2026-11-05",
        "description": "Three TravelPlanner-style requests with explicit constraints.",
    },
    "efficiency": {
        "name": "Efficiency",
        "file": "efficiency.json",
        "kind": "efficiency",
        "version": "2026-11-05",
        "description": "Model calls and tokens per routed turn.",
    },
    "edit_vs_replan": {
        "name": "Edit vs replan",
        "file": "edit_vs_replan.json",
        "kind": "edit",
        "version": "2026-11-05",
        "description": "A local edit keeps the plan and costs less than a replan.",
    },
}

# The headline numbers the v4 report recorded, for the delta column.
BASELINES: dict[str, dict[str, float]] = {
    "routing": {"intent_accuracy": 76.2},
    "injection": {"containment": 98.8},
    "hongkong": {"cases_passed": 8, "constraints_met": 100},
    "japan": {"cases_passed": 6, "grounding": 100, "constraints_met": 100},
    "travelplanner": {"cases_passed": 3, "constraints_met": 100},
    "efficiency": {},
    "edit_vs_replan": {"preservation": 100},
}


@dataclass
class Case:
    id: str
    input: str
    kind: str
    destination: str = ""
    origin: str = ""
    start_date: str | None = None
    days: int | None = None
    party_size: int | None = None
    budget: float | None = None
    currency: str = "HKD"
    constraints: list[str] = field(default_factory=list)
    fault: str | None = None
    fault_detail: dict[str, Any] = field(default_factory=dict)
    expected_intent: str = ""
    split: str = "dev"
    raw: dict[str, Any] = field(default_factory=dict)

    def start(self) -> date:
        if self.start_date:
            try:
                return date.fromisoformat(self.start_date)
            except ValueError:
                pass
        return date.today() + timedelta(days=30)


def load(dataset_id: str) -> list[Case]:
    meta = DATASETS.get(dataset_id)
    if meta is None:
        raise KeyError(dataset_id)
    path = FIXTURES / meta["file"]
    text = path.read_text(encoding="utf-8").strip()
    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        parsed = json.loads(text)
        rows = parsed if isinstance(parsed, list) else [parsed]
    out: list[Case] = []
    for row in rows:
        out.append(_case(row, meta["kind"]))
    return out


def _case(row: dict[str, Any], kind: str) -> Case:
    return Case(
        id=str(row.get("id", "")),
        input=str(row.get("input", "")),
        kind=kind,
        destination=str(row.get("destination") or ""),
        origin=str(row.get("origin") or ""),
        start_date=row.get("start_date"),
        days=row.get("days"),
        party_size=row.get("party_size"),
        budget=row.get("budget"),
        currency=str(row.get("currency") or "HKD"),
        constraints=[str(text) for text in (row.get("constraints") or [])],
        fault=row.get("fault"),
        fault_detail=row.get("fault_detail") or {},
        expected_intent=str(row.get("expected_intent") or ""),
        split=str(row.get("split") or "dev"),
        raw=row,
    )


def suite_list() -> list[dict[str, Any]]:
    frozen = _frozen_flags()
    rows = []
    for dataset_id, meta in DATASETS.items():
        try:
            count = len(load(dataset_id))
        except (OSError, ValueError):
            count = 0
        rows.append(
            {
                "id": dataset_id,
                "name": meta["name"],
                "version": meta["version"],
                "split": "all",
                "frozen": bool(frozen.get(dataset_id, True)),
                "item_count": count,
                "created_at": "2026-11-05T00:00:00Z",
                "description": meta["description"],
            }
        )
    return rows


def preview(dataset_id: str, limit: int = 40) -> list[dict[str, Any]]:
    meta = DATASETS[dataset_id]
    rows = []
    for case in load(dataset_id)[:limit]:
        expected = case.expected_intent or "; ".join(case.constraints[:2]) or "a plan"
        rows.append(
            {
                "case_id": case.id,
                "input": case.input,
                "split": case.split,
                "expected": expected,
                "dataset": meta["name"],
            }
        )
    return rows


def reference_catalog() -> dict[str, Any]:
    """The reference browser on the Evaluation tab: one set per regional catalogue."""
    themes = {
        "hongkong": "Hong Kong",
        "japan": "Japan",
        "china": "China",
        "taiwan": "Taiwan",
    }
    sets = []
    regional = FIXTURES.parent / "regional"
    for name, title in themes.items():
        path = regional / f"{name}.jsonl"
        if not path.exists():
            continue
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        groups: dict[str, list[dict[str, str]]] = {}
        for row in rows:
            key = str(row.get("themes") or row.get("theme") or row.get("category") or "general")
            groups.setdefault(key, []).append(
                {"id": str(row.get("id", "")), "input": str(row.get("input", "")), "expected": str(row.get("expected") or row.get("expected_intent") or "")}
            )
        sets.append(
            {
                "id": f"regional-{name}",
                "kind": "regional",
                "title": title,
                "count": len(rows),
                "shapes": len(groups),
                "groups": [
                    {"id": key, "title": key.replace("_", " ").title(), "count": len(value), "rows": value[:40]}
                    for key, value in groups.items()
                ],
            }
        )
    tp = FIXTURES / "travelplanner.json"
    if tp.exists():
        rows = json.loads(tp.read_text(encoding="utf-8"))
        sets.insert(
            0,
            {
                "id": "travelplanner",
                "kind": "travelplanner",
                "title": "TravelPlanner-style",
                "count": len(rows),
                "shapes": 3,
                "groups": [
                    {
                        "id": "sample",
                        "title": "Sample requests",
                        "count": len(rows),
                        "rows": [
                            {
                                "id": str(row.get("id", "")),
                                "input": str(row.get("input", "")),
                                "expected": "; ".join(row.get("constraints") or []),
                            }
                            for row in rows
                        ],
                    }
                ],
            },
        )
    return {"sets": sets}


def _frozen_flags() -> dict[str, bool]:
    path = FIXTURES.parent / "splits" / "split-20261009.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {}
    frozen = payload.get("frozen") if isinstance(payload, dict) else None
    return frozen if isinstance(frozen, dict) else {}
