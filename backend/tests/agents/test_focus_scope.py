"""Chat focus: validation against the current plan (design 4.1)."""

from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

from backend.agents.focus import check_focus, locked_note, scope_change, scoped_agents
from backend.agents.plan_ops import PlanRequestError
from backend.agents.prompts import ROUTER_FOCUS_INSTRUCTION, data_message
from backend.agents.runtime.mock_handlers import route_handler
from backend.schemas.common import AgentName, Money
from backend.schemas.llm import LLMRequest
from backend.schemas.routing import ChangeRequest, FocusKind, PlanFocus
from backend.tests.agents.helpers import make_trip_plan


@pytest.mark.parametrize(
    ("kind", "focus_id"),
    [
        (FocusKind.DAY, "2026-04-11"),
        (FocusKind.ITEM, "temple@2026-04-10"),
        (FocusKind.HOTEL, "h1"),
        (FocusKind.TICKET, "ret-1"),
    ],
)
def test_check_focus_accepts_every_part_of_the_plan(kind: FocusKind, focus_id: str) -> None:
    check_focus(make_trip_plan(), PlanFocus(kind=kind, id=focus_id))


def test_check_focus_without_a_plan_is_a_conflict() -> None:
    with pytest.raises(PlanRequestError) as caught:
        check_focus(None, PlanFocus(kind=FocusKind.ITEM, id="temple@2026-04-10"))
    assert caught.value.status_code == 409
    assert caught.value.message == "session has no plan"


@pytest.mark.parametrize(
    ("kind", "focus_id"),
    [
        (FocusKind.DAY, "2026-04-12"),  # outside the trip
        (FocusKind.DAY, "20260410"),  # not the ISO form the plan uses
        (FocusKind.ITEM, "nope"),
        (FocusKind.HOTEL, "another-hotel"),
        (FocusKind.TICKET, "temple@2026-04-10"),  # an item id is not a ticket id
    ],
)
def test_check_focus_rejects_ids_not_in_the_plan(kind: FocusKind, focus_id: str) -> None:
    with pytest.raises(PlanRequestError) as caught:
        check_focus(make_trip_plan(), PlanFocus(kind=kind, id=focus_id))
    assert caught.value.status_code == 422
    assert focus_id in caught.value.message


def test_check_focus_on_a_plan_without_a_hotel_is_unknown() -> None:
    plan = make_trip_plan().model_copy(update={"hotel": None})
    with pytest.raises(PlanRequestError) as caught:
        check_focus(plan, PlanFocus(kind=FocusKind.HOTEL, id="h1"))
    assert caught.value.status_code == 422


def test_plan_focus_rejects_an_unknown_kind_and_an_empty_id() -> None:
    with pytest.raises(ValidationError):
        PlanFocus.model_validate({"kind": "restaurant", "id": "x"})
    with pytest.raises(ValidationError):
        PlanFocus.model_validate({"kind": "item", "id": ""})


# ---- mock router honours the focus (deterministic, for scope tests) -------------------------------


def _router_request(focus: dict[str, str] | None, message: str) -> LLMRequest:
    data: list[tuple[str, str]] = [("current_plan", '{"plan_id": "p"}')]
    if focus is not None:
        data.append(("focus", json.dumps(focus)))
    data.append(("user_message", message))
    return LLMRequest(purpose="router", model="mock", messages=[data_message(ROUTER_FOCUS_INSTRUCTION, data)])


@pytest.mark.parametrize(
    ("kind", "part"), [("item", "attraction"), ("day", "attraction"), ("hotel", "hotel"), ("ticket", "ticket")]
)
def test_mock_router_names_the_focused_part_when_the_message_names_none(kind: str, part: str) -> None:
    decision = json.loads(route_handler(_router_request({"kind": kind, "id": "x"}, "Swap this for something else")))
    assert decision["route"] == "modify" and decision["affected_parts"] == [part]


def test_mock_router_keeps_the_parts_the_message_names() -> None:
    named = json.loads(route_handler(_router_request({"kind": "hotel", "id": "x"}, "Swap this for a museum")))
    assert named["affected_parts"] == ["attraction"]  # the code-level scope rule then applies
    unfocused = json.loads(route_handler(_router_request(None, "Swap this for something else")))
    assert unfocused["affected_parts"] == []


# ---- scope: which agent runs and what the change may touch (design 4.1) ---------------------------


@pytest.mark.parametrize(
    ("kind", "parts", "expected"),
    [
        (FocusKind.ITEM, [AgentName.ATTRACTION], [AgentName.ATTRACTION]),  # intersection
        (FocusKind.ITEM, [AgentName.ATTRACTION, AgentName.HOTEL], [AgentName.ATTRACTION]),
        (FocusKind.ITEM, [AgentName.HOTEL], [AgentName.ATTRACTION]),  # disjoint: the focus wins
        (FocusKind.DAY, [], [AgentName.ATTRACTION]),  # router named none: replaced by the mapping
        (FocusKind.HOTEL, [], [AgentName.HOTEL]),
        (FocusKind.TICKET, [AgentName.WEATHER, AgentName.TICKET], [AgentName.TICKET]),
    ],
)
def test_scoped_agents_is_always_the_focused_agent(
    kind: FocusKind, parts: list[AgentName], expected: list[AgentName]
) -> None:
    assert scoped_agents(parts, PlanFocus(kind=kind, id="x")) == expected


EVERYTHING = ChangeRequest(
    start_date=date(2026, 4, 12),
    party_size=4,
    budget=Money(amount=100_000, currency="JPY"),
    hotel_style="luxury",
    replace_hotel=True,
    cheaper_hotel=True,
    remove_place_ids=["temple", "shrine", "museum"],
    add_requests=["aquarium"],
    replace_focus=True,
    refresh=[AgentName.WEATHER],
    summary="everything at once",
)


def test_scope_change_for_an_item_keeps_only_the_replacement_request() -> None:
    scoped = scope_change(EVERYTHING, make_trip_plan(), PlanFocus(kind=FocusKind.ITEM, id="temple@2026-04-10"))
    assert scoped == ChangeRequest(add_requests=["aquarium"], replace_focus=True, summary="everything at once")


def test_scope_change_for_a_day_keeps_removals_of_its_unconfirmed_stops() -> None:
    scoped = scope_change(EVERYTHING, make_trip_plan(), PlanFocus(kind=FocusKind.DAY, id="2026-04-10"))
    # shrine is confirmed on day 1 and museum is on day 2: only temple may go
    assert scoped == ChangeRequest(remove_place_ids=["temple"], add_requests=["aquarium"], summary="everything at once")


def test_scope_change_for_the_hotel_keeps_hotel_fields_only() -> None:
    scoped = scope_change(
        ChangeRequest(replace_focus=True, hotel_style="budget", start_date=date(2026, 4, 12), add_requests=["x"]),
        make_trip_plan(),
        PlanFocus(kind=FocusKind.HOTEL, id="h1"),
    )
    assert scoped == ChangeRequest(hotel_style="budget", replace_hotel=True)


def test_scope_change_for_a_ticket_keeps_only_the_replacement_request() -> None:
    scoped = scope_change(EVERYTHING, make_trip_plan(), PlanFocus(kind=FocusKind.TICKET, id="out-1"))
    assert scoped == ChangeRequest(replace_focus=True, summary="everything at once")


def test_locked_note_names_a_confirmed_part_only() -> None:
    plan = make_trip_plan()
    assert locked_note(plan, PlanFocus(kind=FocusKind.ITEM, id="temple@2026-04-10")) is None
    note = locked_note(plan, PlanFocus(kind=FocusKind.ITEM, id="shrine@2026-04-10"))
    assert note == "Shrine is locked; unlock it first to change it."
    assert locked_note(plan, PlanFocus(kind=FocusKind.DAY, id="2026-04-10")) is None
    assert locked_note(plan, PlanFocus(kind=FocusKind.HOTEL, id="h1")) is None
    assert plan.hotel is not None
    locked = plan.model_copy(
        update={
            "hotel": plan.hotel.model_copy(update={"confirmed": True}),
            "tickets": [t.model_copy(update={"confirmed": True}) for t in plan.tickets],
        }
    )
    assert locked_note(locked, PlanFocus(kind=FocusKind.HOTEL, id="h1")) == (
        "Piece Hostel is locked; unlock it first to change it."
    )
    assert locked_note(locked, PlanFocus(kind=FocusKind.TICKET, id="ret-1")) == (
        "The return train is locked; unlock it first to change it."
    )
