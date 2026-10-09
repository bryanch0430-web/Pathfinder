"""Chat focus: validation against the current plan (design 4.1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.agents.focus import check_focus
from backend.agents.plan_ops import PlanRequestError
from backend.schemas.routing import FocusKind, PlanFocus
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
