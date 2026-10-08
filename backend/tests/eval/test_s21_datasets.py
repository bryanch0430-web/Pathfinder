"""Scenario 21: fixture loaders validate every line, and the TravelPlanner loader parses the
official fields from a locally downloaded file (never the network)."""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

import pytest

from backend.eval.datasets import (
    INJECTION_PROBES_FILE,
    JAPAN_SCENARIOS_FILE,
    FixtureError,
    load_hk_scenarios,
    load_injection_probes,
    load_japan_scenarios,
    load_router_labels,
    load_travelplanner,
)
from backend.eval.models import ProbeChannel, ProbeTarget
from backend.schemas.common import Route
from backend.tests.eval.conftest import append_line


def test_s21_all_four_fixture_sets_load_and_validate() -> None:
    labels, probes = load_router_labels(), load_injection_probes()
    hk, japan = load_hk_scenarios(), load_japan_scenarios()
    assert (len(labels), len(probes), len(hk), len(japan)) == (80, 25, 15, 18)
    assert {label.label for label in labels} == set(Route)
    for p in probes:
        assert (p.field is not None) == (p.channel is ProbeChannel.CONTEXT_FIELD), p.id
        assert (p.agent is not None) == (p.target is ProbeTarget.AGENT), p.id
    assert any(s.edit is not None for s in japan)  # the edit-path metric has pairs to run


def test_s21_invalid_fixture_line_fails_loudly(fixtures_copy: Path) -> None:
    append_line(fixtures_copy / JAPAN_SCENARIOS_FILE, '{"id": "j-bad", "city": "Kyoto"}')
    with pytest.raises(FixtureError, match=rf"{JAPAN_SCENARIOS_FILE}:\d+"):
        load_japan_scenarios(fixtures_copy)


def test_s21_duplicate_fixture_id_fails(fixtures_copy: Path) -> None:
    path = fixtures_copy / INJECTION_PROBES_FILE
    append_line(path, path.read_text(encoding="utf-8").splitlines()[0])
    with pytest.raises(FixtureError, match="duplicate id 'p01'"):
        load_injection_probes(fixtures_copy)


OFFICIAL_ROW = {
    "org": "Sarasota",
    "dest": "Chicago",
    "days": "3",
    "visiting_city_number": "1",
    "date": "['2022-03-22', '2022-03-23', '2022-03-24']",
    "people_number": "1",
    "local_constraint": "{'house rule': None, 'cuisine': None, 'room type': 'entire room', 'transportation': None}",
    "budget": "1900",
    "query": "Please help me plan a trip from Sarasota to Chicago for 3 days.",
    "level": "easy",
    "reference_information": "[{'Description': 'big cell'}]",
}


def test_s21_travelplanner_csv_parses_official_fields(tmp_path: Path) -> None:
    path = tmp_path / "validation.csv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(OFFICIAL_ROW))
        writer.writeheader()
        writer.writerow(OFFICIAL_ROW)
        writer.writerow({**OFFICIAL_ROW, "people_number": "4", "local_constraint": "{'cuisine': 'Mexican'}"})
    queries = load_travelplanner("validation", tmp_path)  # a folder: finds validation.csv
    assert [q.idx for q in queries] == [0, 1]
    first = queries[0]
    assert (first.org, first.dest, first.days, first.people_number, first.budget) == ("Sarasota", "Chicago", 3, 1, 1900)
    assert first.date == [date(2022, 3, 22), date(2022, 3, 23), date(2022, 3, 24)]
    assert first.local_constraint.room_type == "entire room" and first.local_constraint.house_rule is None
    assert first.level == "easy" and first.visiting_city_number == 1
    assert queries[1].local_constraint.cuisine == ["Mexican"] and queries[1].people_number == 4


def test_s21_travelplanner_jsonl_and_json(tmp_path: Path) -> None:
    row = {
        **OFFICIAL_ROW,
        "days": 5,
        "people_number": 2,
        "budget": 3000,
        "date": ["2022-03-01", "2022-03-05"],
        "local_constraint": {"house rule": "pets", "cuisine": ["Chinese", "Indian"]},
    }
    jsonl = tmp_path / "test.jsonl"
    jsonl.write_text(json.dumps(row) + "\n\n" + json.dumps(row) + "\n", encoding="utf-8")
    queries = load_travelplanner("test", jsonl)
    assert len(queries) == 2 and queries[0].days == 5
    assert queries[0].local_constraint.house_rule == "pets"
    assert queries[0].local_constraint.cuisine == ["Chinese", "Indian"]
    wrapped = tmp_path / "train.json"
    wrapped.write_text(json.dumps({"data": [row]}), encoding="utf-8")
    assert len(load_travelplanner("train", wrapped)) == 1


def test_s21_travelplanner_missing_file_explains_the_download(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError) as info:
        load_travelplanner("validation", tmp_path)
    message = str(info.value)
    assert "osunlp/TravelPlanner" in message and "validation.*" in message and "README.md" in message
