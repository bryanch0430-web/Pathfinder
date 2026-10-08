"""Fixture loaders for the custom evaluation sets and a loader stub for TravelPlanner.

Custom sets live as JSON lines under `backend/eval/fixtures/`. TravelPlanner (Xie et al. 2024,
HF dataset `osunlp/TravelPlanner`) is NOT vendored: the proposal says "TravelPlanner uses the
official split", so the official files are downloaded by hand to a local path (see
`fixtures/travelplanner/README.md`). No code here touches the network.
"""

from __future__ import annotations

import ast
import csv
import json
from collections.abc import Callable, Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.eval.models import HKScenario, InjectionProbe, JapanScenario, RouterLabel

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
TRAVELPLANNER_DIR = FIXTURES_DIR / "travelplanner"

ROUTER_LABELS_FILE = "router_labels.jsonl"
INJECTION_PROBES_FILE = "injection_probes.jsonl"
HK_SCENARIOS_FILE = "hk_scenarios.jsonl"
JAPAN_SCENARIOS_FILE = "japan_scenarios.jsonl"

_Model = TypeVar("_Model", bound=BaseModel)


class FixtureError(ValueError):
    """A fixture line is not valid JSON or does not match its model."""


def _read_jsonl(path: Path, model: type[_Model]) -> list[_Model]:
    out: list[_Model] = []
    seen: set[str] = set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = model.model_validate_json(line)
        except ValidationError as exc:
            raise FixtureError(f"{path.name}:{lineno}: {exc}") from exc
        item_id = getattr(item, "id", None)
        if isinstance(item_id, str):
            if item_id in seen:
                raise FixtureError(f"{path.name}:{lineno}: duplicate id {item_id!r}")
            seen.add(item_id)
        out.append(item)
    return out


def load_router_labels(fixtures_dir: Path = FIXTURES_DIR) -> list[RouterLabel]:
    return _read_jsonl(fixtures_dir / ROUTER_LABELS_FILE, RouterLabel)


def load_injection_probes(fixtures_dir: Path = FIXTURES_DIR) -> list[InjectionProbe]:
    return _read_jsonl(fixtures_dir / INJECTION_PROBES_FILE, InjectionProbe)


def load_hk_scenarios(fixtures_dir: Path = FIXTURES_DIR) -> list[HKScenario]:
    return _read_jsonl(fixtures_dir / HK_SCENARIOS_FILE, HKScenario)


def load_japan_scenarios(fixtures_dir: Path = FIXTURES_DIR) -> list[JapanScenario]:
    return _read_jsonl(fixtures_dir / JAPAN_SCENARIOS_FILE, JapanScenario)


# ------------------------------------------------------------------------------------------------
# TravelPlanner (stub: parse the official fields; scoring with the official script is a TODO)
# ------------------------------------------------------------------------------------------------

TravelPlannerSplit = Literal["train", "validation", "test"]
_SUFFIXES = (".jsonl", ".json", ".csv")


class TravelPlannerLocalConstraint(BaseModel):
    """The official `local_constraint` dict ('house rule', 'cuisine', 'room type',
    'transportation'); each is None when the query does not state it."""

    model_config = ConfigDict(extra="ignore")

    house_rule: str | None = None
    cuisine: list[str] | None = None
    room_type: str | None = None
    transportation: str | None = None


class TravelPlannerQuery(BaseModel):
    """The fields of one official TravelPlanner query this harness can use. Unknown columns
    (reference_information, annotated_plan, ...) are ignored."""

    model_config = ConfigDict(extra="ignore")

    idx: int = Field(ge=0, description="Row index in the official split file")
    query: str
    org: str
    dest: str
    days: int = Field(ge=1)
    visiting_city_number: int | None = None
    date: list[date]
    people_number: int = Field(ge=1)
    budget: float | None = None
    local_constraint: TravelPlannerLocalConstraint = Field(default_factory=TravelPlannerLocalConstraint)
    level: str | None = None


_INSTRUCTIONS = (
    "TravelPlanner data not found. Download the official split from the Hugging Face dataset "
    "'osunlp/TravelPlanner' (validation or test) and save it as "
    "backend/eval/fixtures/travelplanner/<split>.csv (or .json / .jsonl), or pass its path. "
    "See backend/eval/fixtures/travelplanner/README.md."
)


def _resolve_travelplanner_path(split: TravelPlannerSplit, path: Path | None) -> Path:
    if path is not None and path.is_file():
        return path
    folder = path if path is not None and path.is_dir() else TRAVELPLANNER_DIR
    for suffix in _SUFFIXES:
        candidate = folder / f"{split}{suffix}"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"{_INSTRUCTIONS} (looked for {split}.* in {folder})")


def _literal(value: object) -> object:
    """Official CSV cells hold Python literals ("['2022-03-16', ...]", "{'house rule': None}").
    ast.literal_eval parses literals only (never executes code)."""
    if isinstance(value, str):
        text = value.strip()
        if text[:1] in ("[", "{", "("):
            try:
                return ast.literal_eval(text)
            except (ValueError, SyntaxError):
                return value
        if text in ("", "None", "nan"):
            return None
    return value


def _local_constraint(value: object) -> TravelPlannerLocalConstraint:
    parsed = _literal(value)
    if not isinstance(parsed, Mapping):
        return TravelPlannerLocalConstraint()
    norm = {str(k).strip().lower().replace(" ", "_"): v for k, v in parsed.items()}
    cuisine = norm.get("cuisine")
    if isinstance(cuisine, str):
        cuisine = [cuisine]
    return TravelPlannerLocalConstraint.model_validate({**norm, "cuisine": cuisine})


def _row_to_query(idx: int, row: Mapping[str, object]) -> TravelPlannerQuery:
    raw_dates = _literal(row.get("date"))
    dates = raw_dates if isinstance(raw_dates, list | tuple) else ([raw_dates] if raw_dates else [])
    data: dict[str, object] = {
        "idx": idx,
        "query": row.get("query"),
        "org": row.get("org"),
        "dest": row.get("dest"),
        "days": row.get("days"),
        "visiting_city_number": _literal(row.get("visiting_city_number")),
        "date": list(dates),
        "people_number": row.get("people_number"),
        "budget": _literal(row.get("budget")),
        "level": _literal(row.get("level")),
    }
    query = TravelPlannerQuery.model_validate(data)
    return query.model_copy(update={"local_constraint": _local_constraint(row.get("local_constraint"))})


def _rows(path: Path) -> Iterator[Mapping[str, object]]:
    if path.suffix == ".csv":
        csv.field_size_limit(2**31 - 1)  # reference_information cells are large
        with path.open(encoding="utf-8", newline="") as fh:
            yield from csv.DictReader(fh)
        return
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        for line in text.splitlines():
            if line.strip():
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    yield parsed
        return
    parsed_doc = json.loads(text)
    rows = parsed_doc.get("data", []) if isinstance(parsed_doc, dict) else parsed_doc
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict):
            yield row


def load_travelplanner(
    split: TravelPlannerSplit, path: Path | None = None
) -> list[TravelPlannerQuery]:
    """Parse a locally downloaded official TravelPlanner split into typed queries.

    Raises FileNotFoundError (with download instructions) when the file is missing.

    TODO(eval): map Pathfinder output to the official TravelPlanner evaluation script
    (commonsense + hard-constraint pass rates over its USA sandbox). Not in scope: Pathfinder's
    mock providers do not serve the TravelPlanner sandbox, so these queries are loaded and
    counted but not scored yet.
    """
    resolved = _resolve_travelplanner_path(split, path)
    parse: Callable[[int, Mapping[str, object]], TravelPlannerQuery] = _row_to_query
    return [parse(i, row) for i, row in enumerate(_rows(resolved))]
