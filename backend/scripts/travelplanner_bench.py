"""Official TravelPlanner queries through the committed reader, with mocked travel calls.

The 1,225 queries are the public train, validation and test splits. No live flight, hotel or
place API is called. A mock answers only when the request has the fields a real call needs.
The writer is not called: the reader is the rules path from the last committed understand.
"""

from __future__ import annotations

import ast
import asyncio
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path

from app.application.llm_gateway import LLMGateway
from app.application.prompts import PromptLibrary
from app.application.understanding import understand
from app.config import Settings
from app.domain.models import TripContext
from app.infrastructure.llm import NullLLM
from app.infrastructure.observability import NoopTracer

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "travelplanner"
SPLITS = ("train", "validation", "test")
TODAY = date(2022, 2, 28)
REPORT = ROOT / "data" / "travelplanner" / "sandbox-report.json"


def load_rows() -> list[dict]:
    rows: list[dict] = []
    for split in SPLITS:
        path = DATA / f"travelplanner_{split}.csv"
        with path.open(encoding="utf-8") as handle:
            for index, row in enumerate(csv.DictReader(handle)):
                rows.append(
                    {
                        "split": split,
                        "index": index,
                        "org": row["org"].strip(),
                        "dest": row["dest"].strip(),
                        "days": int(row["days"]),
                        "cities": int(row["visiting_city_number"]),
                        "people": int(row["people_number"]),
                        "budget": float(row["budget"]),
                        "dates": [date.fromisoformat(day) for day in ast.literal_eval(row["date"])],
                        "level": row.get("level") or "",
                        "constraint": ast.literal_eval(row["local_constraint"]),
                        "query": row["query"],
                    }
                )
    return rows


def _same(left: str, right: str) -> bool:
    return " ".join(left.casefold().split()) == " ".join(right.casefold().split())


class MockTravel:
    """Returns a row only when the call has the fields a live search requires."""

    def flight(self, request: dict) -> dict:
        self._need(request, ("origin", "destination", "depart_date", "party_size"))
        if _same(request["origin"], request["destination"]):
            raise ValueError("origin and destination are the same")
        if request["party_size"] < 1:
            raise ValueError("party")
        return {"airline": "Mock Air", "origin": request["origin"], "destination": request["destination"], "price": 350}

    def hotel(self, request: dict) -> dict:
        self._need(request, ("destination", "check_in", "check_out", "party_size"))
        if request["check_out"] <= request["check_in"]:
            raise ValueError("stay ends before it starts")
        return {"name": f"{request['destination']} Inn", "nightly": 120}

    def place(self, request: dict) -> dict:
        self._need(request, ("destination", "query"))
        return {"name": request["query"], "destination": request["destination"]}

    @staticmethod
    def _need(request: dict, keys: tuple[str, ...]) -> None:
        missing = [key for key in keys if request.get(key) in (None, "")]
        if missing:
            raise ValueError("missing " + ", ".join(missing))


def tool_requests(context: TripContext, constraint: dict) -> tuple[list[str], list[str]]:
    """The calls this reading would make, and the ones the mock refused."""
    mock = MockTravel()
    made: list[str] = []
    refused: list[str] = []
    transport = constraint.get("transportation")
    if transport != "no flight":
        try:
            mock.flight(
                {
                    "origin": context.origin,
                    "destination": context.destination,
                    "depart_date": context.start_date,
                    "party_size": context.party_size,
                }
            )
            made.append("flight")
        except ValueError:
            refused.append("flight")
    stay_end = context.end_date
    try:
        mock.hotel(
            {
                "destination": context.destination,
                "check_in": context.start_date,
                "check_out": stay_end,
                "party_size": context.party_size,
            }
        )
        made.append("hotel")
    except ValueError:
        refused.append("hotel")
    try:
        mock.place({"destination": context.destination, "query": context.destination})
    except ValueError:
        refused.append("place")
    else:
        made.append("place")
    return made, refused


async def run() -> dict:
    settings = Settings()
    gateway = LLMGateway(NullLLM(), PromptLibrary(settings), NoopTracer(), settings)
    slots = Counter()
    calls = Counter()
    by_split: dict[str, Counter] = {split: Counter() for split in SPLITS}
    misses: list[dict] = []
    for row in load_rows():
        context = await understand(row["query"], TripContext(session_id="tp"), gateway, today=TODAY)
        made, refused = tool_requests(context, row["constraint"])
        matched = {
            "destination": _same(context.destination or "", row["dest"]),
            "origin": _same(context.origin or "", row["org"]),
            "days": context.days == row["days"] or (
                context.start_date is not None
                and context.end_date is not None
                and (context.end_date - context.start_date).days + 1 == row["days"]
            ),
            "party": context.party_size == row["people"],
            "budget": context.budget == row["budget"],
        }
        request_ok = not refused
        for key, good in matched.items():
            slots[key] += int(good)
        slots["queries"] += 1
        slots["request_ok"] += int(request_ok)
        bucket = by_split[row["split"]]
        bucket["queries"] += 1
        bucket["request_ok"] += int(request_ok)
        bucket["destination"] += int(matched["destination"])
        for name in made:
            calls[name] += 1
        for name in refused:
            calls["refused_" + name] += 1
        if not request_ok and len(misses) < 8:
            misses.append(
                {
                    "split": row["split"],
                    "level": row["level"],
                    "gold": f"{row['org']} -> {row['dest']}, {row['days']} days, {row['people']} people",
                    "read": f"{context.origin or '-'} -> {context.destination or '-'}, days={context.days}, party={context.party_size}",
                    "refused": refused,
                }
            )
    report = {
        "dataset": "osunlp/TravelPlanner",
        "queries": slots["queries"],
        "reader": "committed understand, rules only, writer not called",
        "tools": "mock; a call is answered only when its required fields are present",
        "today": TODAY.isoformat(),
        "slots_matched": {key: slots[key] for key in ("destination", "origin", "days", "party", "budget")},
        "well_formed_requests": slots["request_ok"],
        "calls": dict(calls),
        "by_split": {split: dict(by_split[split]) for split in SPLITS},
        "sample_refusals": misses,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> None:
    report = asyncio.run(run())
    total = report["queries"]
    print(f"queries {total}")
    for key, count in report["slots_matched"].items():
        print(f"  {key}: {count}/{total}")
    print(f"well-formed tool requests: {report['well_formed_requests']}/{total}")
    print("calls", report["calls"])
    for split, row in report["by_split"].items():
        print(f"  {split}: destination {row['destination']}/{row['queries']}, requests {row['request_ok']}/{row['queries']}")


if __name__ == "__main__":
    main()
