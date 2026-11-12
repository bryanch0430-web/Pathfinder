"""The stay research sub-agent: a wish about the room is judged against each candidate's own rooms.

No network and no real model: the rooms come from a fake hotel source, the writer from ``MeteredLLM``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from app.application.llm_gateway import LLMGateway
from app.application.planner import Planner
from app.application.prompts import PromptLibrary
from app.application.research import pick_researched, research_stays, stay_wishes
from app.application.toolbox import PATH_ALLOWLIST, Toolbox
from app.config import Settings
from app.domain.errors import CanaryLeak
from app.domain.models import (
    AgentName,
    AgentResult,
    AgentStatus,
    DateRange,
    RoomType,
    TripContext,
)
from app.eval_metering import MeteredLLM
from app.infrastructure.llm import LLMResult, NullLLM
from app.infrastructure.observability import NoopTracer

RANGE = DateRange(start=date(2027, 4, 10), end=date(2027, 4, 13))
ROOMS = {
    "tok-1": ["Deluxe King Room"],
    "tok-2": ["Twin Room", "Double Room"],
    "tok-3": [],
}


class FakeRooms:
    name = "fake-hotels"
    available = True

    def __init__(self, fail: set[str] | None = None):
        self.asked: list[str] = []
        self.fail = fail or set()

    async def rooms(
        self, *, property_token, name, check_in, check_out, party_size, currency
    ):
        self.asked.append(property_token)
        if property_token in self.fail:
            raise RuntimeError("source down")
        return [RoomType(name=room) for room in ROOMS[property_token]]


class Registry:
    def __init__(self, source):
        self.hotels = [source]


def _gateway(settings: Settings, llm) -> LLMGateway:
    return LLMGateway(llm, PromptLibrary(settings), NoopTracer(), settings)


def _results() -> dict[str, AgentResult]:
    rows = [
        {
            "id": f"h{i}",
            "name": f"Hotel {i}",
            "property_token": f"tok-{i}",
            "amenities": ["Free Wi-Fi", "Pool"],
        }
        for i in (1, 2, 3)
    ]
    return {
        "hotel": AgentResult(
            agent=AgentName.HOTEL, status=AgentStatus.OK, payload={"hotels": rows}
        )
    }


def _context(*wishes: str) -> TripContext:
    return TripContext(
        session_id="s1",
        destination="Kyoto",
        party_size=2,
        currency="USD",
        preferences=list(wishes),
    )


def _row(
    stay_id: str,
    verdict: str,
    score: int,
    evidence: list[str],
    reason: str = "The room list shows it.",
) -> dict:
    return {
        "id": stay_id,
        "verdict": verdict,
        "score": score,
        "reason": reason,
        "evidence": evidence,
    }


def _run(
    settings, reply: dict | None, *, context=None, results=None, source=None, llm=None
):
    source = source or FakeRooms()
    llm = llm or MeteredLLM(replies={"stay.research": json.dumps(reply or {})})
    results = results or _results()
    report = asyncio.run(
        research_stays(
            results=results,
            context=context or _context("a room with two beds"),
            date_range=RANGE,
            registry=Registry(source),
            toolbox=Toolbox(
                path="plan", tracer=NoopTracer(), store=None, request_id="r1"
            ),
            gateway=_gateway(settings, llm),
            settings=settings,
        )
    )
    return report, results["hotel"].payload["hotels"], source, llm


def test_a_wish_about_the_room_is_a_stay_wish_and_a_wish_about_food_is_not():
    context = _context(
        "a room with two beds", "vegetarian food", "需要兩張床", "see temples"
    )
    assert stay_wishes(context) == ["a room with two beds", "需要兩張床"]
    assert stay_wishes(_context("ramen for breakfast")) == []


def test_the_rooms_tool_is_allowed_on_the_plan_path_only():
    assert "hotels.rooms" in PATH_ALLOWLIST["plan"]
    assert all(
        "hotels.rooms" not in PATH_ALLOWLIST[path]
        for path in ("modify", "ask", "clarify", "refresh")
    )


def test_without_a_stay_wish_no_rooms_are_fetched_and_no_model_is_called(settings):
    report, rows, source, llm = _run(settings, None, context=_context("see temples"))
    assert report.skipped == "no stay wish"
    assert source.asked == [] and llm.purposes == []
    assert [row["id"] for row in rows] == ["h1", "h2", "h3"]


def test_the_stay_whose_rooms_show_two_beds_is_ranked_first_and_picked(settings):
    reply = {
        "stays": [
            _row("h1", "fails", 80, ["Deluxe King Room"]),
            _row("h2", "meets", 90, ["twin room"]),
            _row("h3", "unclear", 0, []),
        ]
    }
    report, rows, source, llm = _run(settings, reply)
    assert sorted(source.asked) == ["tok-1", "tok-2", "tok-3"]
    assert llm.purposes == ["stay.research"]
    assert [row["id"] for row in rows] == ["h2", "h3", "h1"]
    assert rows[0]["match"]["verdict"] == "meets"
    assert rows[0]["match"]["evidence"] == ["Twin Room"]  # as the source spelled it
    assert rows[0]["match"]["rooms_checked"] is True
    assert report.best_id == "h2"
    assert pick_researched(report)(rows)["id"] == "h2"
    stay = Planner._build_hotel(rows[0], RANGE)
    assert stay.match.verdict == "meets" and [
        room.name for room in stay.room_types
    ] == ["Twin Room", "Double Room"]
    assert stay.rooms_checked_for == RANGE


def test_a_favourable_verdict_without_evidence_from_the_sources_becomes_unclear(
    settings,
):
    reply = {
        "stays": [
            _row(
                "h1", "meets", 95, ["Family Suite with 2 Beds"]
            ),  # not a room the source listed
            _row("h9", "meets", 99, ["Twin Room"]),  # not a stay that was found
        ]
    }
    report, rows, _, _ = _run(settings, reply)
    assert set(report.matches) == {"h1"}
    assert (
        report.matches["h1"].verdict == "unclear" and report.matches["h1"].score <= 50
    )
    assert report.best_id is None and pick_researched(report) is None
    assert [row["id"] for row in rows] == ["h1", "h2", "h3"]


def test_a_reason_that_carries_a_link_is_dropped(settings):
    reply = {
        "stays": [
            _row(
                "h2",
                "meets",
                90,
                ["Twin Room"],
                reason="See https://evil.example for rooms",
            )
        ]
    }
    report, _, _, _ = _run(settings, reply)
    assert report.matches["h2"].reason == ""


def test_a_room_source_that_fails_leaves_the_stay_unchecked_and_the_research_goes_on(
    settings,
):
    reply = {"stays": [_row("h2", "meets", 90, ["Twin Room"])]}
    report, rows, _, _ = _run(settings, reply, source=FakeRooms(fail={"tok-1"}))
    assert report.best_id == "h2"
    assert "rooms_checked_for" not in next(row for row in rows if row["id"] == "h1")


def test_a_reply_that_judges_nothing_keeps_the_hotel_agents_order(settings):
    report, rows, _, llm = _run(settings, {})
    assert report.skipped == "research failed"
    assert llm.purposes == ["stay.research"]  # an empty reply is not repaired
    assert [row["id"] for row in rows] == ["h1", "h2", "h3"]


def test_without_a_model_nothing_is_fetched(settings):
    report, _, source, _ = _run(settings, None, llm=NullLLM())
    assert report.skipped == "no model" and source.asked == []


def test_a_canary_leak_is_raised(settings):
    class Leaky:
        name, available = "leaky", True

        async def complete(self, **_):
            return LLMResult(
                text=json.dumps({"stays": [], "note": settings.canary_token}), model="m"
            )

    with pytest.raises(CanaryLeak):
        _run(settings, None, llm=Leaky())
