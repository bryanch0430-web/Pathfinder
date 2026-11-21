"""A trusted planner draft keeps its order; open day requests can rewrite named days."""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta

import pytest

from app.application.day_rewrite import (
    looks_like_open_day_request,
    should_rewrite,
    touch_days,
)
from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner, PlannerAssignment, PlannerDraft, _long_leg_note
from app.application.prompts import PromptLibrary
from app.config import Settings
from app.domain.models import (
    DateRange,
    DayPlan,
    Destination,
    GeoPoint,
    ItemType,
    PlanItem,
    SourceRef,
    TripContext,
    TripPlan,
)
from app.infrastructure.observability import NoopTracer
from app.infrastructure.providers.fx import FxService
from tests.test_empty_days import _compose, _places
from tests.test_planner import _agents, _place
from tests.test_security import RecordingLLM


def test_a_trusted_draft_keeps_a_single_stop_day(settings: Settings) -> None:
    planner = Planner(None, settings, FxService())  # type: ignore[arg-type]
    names = [
        "Alpha Hall", "Beta Hall", "Gamma Hall", "Delta Hall", "Epsilon Hall",
        "Zeta Hall", "Theta Hall", "Iota Hall", "Kappa Hall",
    ]
    places = [
        _place(f"p{i}", name, 35.0 + i * 0.02, 135.0 + i * 0.02) for i, name in enumerate(names)
    ]
    draft = PlannerDraft(
        assignments=[
            PlannerAssignment(day_index=0, item_ids=["p0", "p1", "p2"], why="Old town first."),
            PlannerAssignment(day_index=1, item_ids=["p3", "p4"]),
            PlannerAssignment(day_index=2, item_ids=["p5"]),
            PlannerAssignment(day_index=3, item_ids=["p6", "p7", "p8"]),
        ]
    )
    assignment = planner._assign_places(places, 4, draft, pace="balanced")
    assert [len(assignment[index]) for index in range(4)] == [3, 2, 1, 3]
    assert [row["id"] for row in assignment[2]] == ["p5"]


def test_a_trusted_draft_does_not_empty_fill_by_stealing_stops(settings: Settings) -> None:
    places = _places(16)
    planner = Planner(None, settings, FxService())  # type: ignore[arg-type]
    packed = [
        {"day_index": index, "item_ids": [places[position]["id"] for position in range(len(places)) if position % 6 == index]}
        for index in range(6)
    ]
    draft = PlannerDraft(assignments=[PlannerAssignment(**row) for row in packed if row["item_ids"]])
    assignment = planner._assign_places(places, 13, draft, pace="balanced")
    counts = [len(assignment[index]) for index in range(13)]
    assert min(counts[:6]) >= 1
    assert all(count == 0 for count in counts[6:]), counts


def test_day_note_keeps_the_draft_why(settings: Settings) -> None:
    places = _places(3)
    plan = _compose(
        settings,
        places,
        2,
        {
            "summary": "Two quiet days.",
            "day_titles": ["Shrines", "Lanes"],
            "assignments": [
                {
                    "day_index": 0,
                    "item_ids": [places[0]["id"], places[1]["id"]],
                    "why": "The shrine opens early.",
                },
                {"day_index": 1, "item_ids": [places[2]["id"]], "why": "One lane only."},
            ],
            "notes": [],
        },
    )
    assert "The shrine opens early." in plan.days[0].note
    assert "One lane only." in plan.days[1].note


def test_long_leg_note_names_the_hop() -> None:
    note = _long_leg_note(
        [
            {"name": "North Gate", "geo": {"lat": 35.0, "lng": 135.0}},
            {"name": "South Gate", "geo": {"lat": 35.1, "lng": 135.1}},
        ]
    )
    assert "North Gate" in note and "South Gate" in note


def test_open_day_request_detection() -> None:
    assert looks_like_open_day_request("make day 2 quieter after the late flight")
    assert looks_like_open_day_request("keep lunch and drop the far temple")
    assert not looks_like_open_day_request("is day 2 too busy?")
    assert should_rewrite(
        message="make day 2 quieter",
        draft_confidence=0.7,
        draft_operation="",
        changed=False,
        asked=False,
    )
    assert not should_rewrite(
        message="swap Fushimi for Kiyomizu",
        draft_confidence=0.9,
        draft_operation="replace",
        changed=False,
        asked=False,
    )


def test_touch_days_reads_the_named_day() -> None:
    start = date.today() + timedelta(days=20)
    plan = TripPlan(
        session_id="s",
        destination=Destination(name="Kyoto"),
        dates=DateRange(start=start, end=start + timedelta(days=2)),
        days=[
            DayPlan(
                date=start + timedelta(days=offset),
                title=f"Day {offset + 1}",
                items=[
                    PlanItem(
                        id=f"a{offset}",
                        type=ItemType.ATTRACTION,
                        name=f"Stop {offset}",
                        start="10:00",
                        end="11:30",
                        source_refs=[SourceRef(title=f"Stop {offset}", source="check")],
                    )
                ],
            )
            for offset in range(3)
        ],
    )
    assert touch_days(plan, "make day 2 quieter", target_day=None) == [2]


class _RewriteLLM(RecordingLLM):
    def __init__(self, reply: dict) -> None:
        super().__init__()
        self.reply = json.dumps(reply)

    async def complete(self, *, system, user, purpose, json_mode=True, temperature=0.2, max_tokens=4096):
        from app.infrastructure.llm import LLMResult

        self.prompts.append(user)
        return LLMResult(text=self.reply, model="reply")


def test_rewrite_days_keeps_untouched_days(settings: Settings) -> None:
    from app.application.day_rewrite import rewrite_days

    start = date.today() + timedelta(days=25)
    places = [
        _place("p1", "First Gate", 35.0, 135.0),
        _place("p2", "Far Temple", 35.2, 135.2),
        _place("p3", "Quiet Lane", 35.01, 135.01),
    ]
    def stop(item_id: str, name: str, start_clock: str, end_clock: str, lat: float, lng: float) -> PlanItem:
        return PlanItem(
            id=item_id,
            type=ItemType.ATTRACTION,
            name=name,
            start=start_clock,
            end=end_clock,
            geo=GeoPoint(lat=lat, lng=lng),
            source_refs=[SourceRef(title=name, source="check")],
        )

    day1 = DayPlan(
        date=start,
        title="Arrival",
        items=[
            stop("p1", "First Gate", "10:00", "11:30", 35.0, 135.0),
            stop("p2", "Far Temple", "14:00", "16:00", 35.2, 135.2),
        ],
    )
    day2 = DayPlan(
        date=start + timedelta(days=1),
        title="Kept",
        items=[stop("p3", "Quiet Lane", "10:00", "12:00", 35.01, 135.01)],
    )
    plan = TripPlan(
        session_id="s",
        destination=Destination(name="Kyoto", geo=GeoPoint(lat=35.0, lng=135.0)),
        dates=DateRange(start=start, end=start + timedelta(days=1)),
        days=[day1, day2],
    )
    llm = _RewriteLLM(
        {
            "summary": "Day 1 drops the far temple so the afternoon stays near the gate.",
            "days": [
                {
                    "day": 1,
                    "item_ids": ["p1"],
                    "dwell_minutes": [120],
                    "title": "A quieter arrival",
                    "why": "The far temple moves off the arrival day.",
                }
            ],
        }
    )
    gateway = LLMGateway(llm, PromptLibrary(settings), NoopTracer(), settings)
    context = TripContext(session_id="s", destination="Kyoto", start_date=start, end_date=start + timedelta(days=1))
    result = asyncio.run(
        rewrite_days(
            plan=plan,
            context=context,
            message="make day 1 quieter and drop the far temple",
            gateway=gateway,
            days=[1],
            places=places,
        )
    )
    assert result is not None
    updated, summary = result
    assert "quieter" in summary.lower() or "far" in summary.lower()
    assert [item.name for item in updated.days[0].items if item.type == ItemType.ATTRACTION] == ["First Gate"]
    assert [item.name for item in updated.days[1].items if item.type == ItemType.ATTRACTION] == ["Quiet Lane"]
    assert updated.days[1].title == "Kept"
