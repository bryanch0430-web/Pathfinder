"""Swapping a hotel the traveller points at replaces it.

The stay that comes back from the hotel search has the id of the candidate it was built from, not
the id of the stay it replaces, so the edit looked for the referred id, found nothing and put the
original back: "swap this hotel" ended in "Nothing in the plan matched that request".
"""

from __future__ import annotations

import asyncio

import pytest

from app.application.modification import ModifyPath
from app.application.toolbox import Toolbox
from app.config import Settings
from app.infrastructure.observability import NoopTracer
from tests.test_modify_targets import _build


def _swap(settings: Settings, message: str, *, select: bool):
    gateway, planner, plan_path, context, plan = _build(settings)
    hotel = plan.hotels[0]
    modify = ModifyPath(planner=planner, plan_path=plan_path, settings=settings, gateway=gateway)
    updated, diff = asyncio.run(
        modify.run(
            plan=plan,
            context=context,
            message=message.format(name=hotel.name),
            route_parts=["hotel"],
            toolbox=Toolbox(path="modify", tracer=NoopTracer(), store=None, request_id="t"),
            tracer=NoopTracer(),
            emit=lambda *_: None,
            scope_item_id=hotel.id if select else None,
        )
    )
    return plan, updated, diff


@pytest.mark.parametrize(
    ("message", "select"),
    [
        ("swap this hotel for another one", True),
        ("swap {name} for another hotel", False),
        ("swap the hotel for another one", False),
    ],
    ids=["chip-selected", "hotel-named", "plain"],
)
def test_swapping_the_hotel_replaces_it(settings: Settings, message: str, select: bool) -> None:
    before, after, diff = _swap(settings, message, select=select)

    assert [hotel.name for hotel in after.hotels] != [hotel.name for hotel in before.hotels], after.change_summary
    assert any(change["kind"] == "hotel" for change in diff), diff
    assert after.version == before.version + 1
    assert "Nothing in the plan matched" not in after.change_summary


def test_the_swapped_stay_keeps_the_trip_dates_and_the_id_of_the_one_it_replaces(settings: Settings) -> None:
    before, after, _ = _swap(settings, "swap this hotel for another one", select=True)

    assert after.hotels[0].id == before.hotels[0].id
    assert (after.hotels[0].check_in, after.hotels[0].check_out) == (before.hotels[0].check_in, before.hotels[0].check_out)
    # Nothing else moved: the stops are the original's.
    assert [item.id for day in after.days for item in day.items] == [item.id for day in before.days for item in day.items]
