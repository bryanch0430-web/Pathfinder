"""The direct-edit grid keeps the plan valid: no overlaps, no stop in another city, no derived rows moved by hand, and the
transfers around a moved stop worked out again."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from plan_samples import make_plan, make_two_city_plan
from pydantic import ValidationError

from app.application.proposals import apply_layout
from app.config import Settings
from app.domain.models import DayPlan, GeoPoint, ItemType, TripContext, TripPlan
from app.interface.api import create_app

GUEST = {"X-Client-Token": "test"}


def _move(item_id: str, day: int, start: str, end: str) -> dict:
    return {"id": item_id, "day": day, "start": start, "end": end}


# -- the model keeps its guarantee -------------------------------------------------------------------------------------


def test_a_day_with_overlapping_items_does_not_validate() -> None:
    plan = make_plan(days=1, with_tickets=False)
    first, second = plan.days[0].items[0], plan.days[0].items[1]
    clashing = second.model_copy(update={"start": first.start, "end": first.end})

    with pytest.raises(ValidationError, match="overlap"):
        DayPlan(date=plan.days[0].date, title="Day 1", items=[first, clashing])


# -- what apply_layout refuses -----------------------------------------------------------------------------------------


def test_a_move_that_overlaps_another_stop_is_refused_with_both_names() -> None:
    plan = make_plan(days=1, with_tickets=False)
    first, lunch = plan.days[0].items[0], plan.days[0].items[1]

    with pytest.raises(ValueError) as refused:
        apply_layout(plan, [_move(lunch.id, 1, "09:20", "10:20")])

    assert (
        str(refused.value)
        == f"{first.name} and {lunch.name} would overlap on {plan.days[0].date}"
    )


def test_a_move_to_a_day_in_another_city_is_refused_with_the_city() -> None:
    plan = make_two_city_plan()
    kyoto_stop = plan.days[0].items[0]
    assert plan.days[0].segment_id == "seg_a" and plan.days[3].segment_id == "seg_b"

    with pytest.raises(ValueError) as refused:
        apply_layout(plan, [_move(kyoto_stop.id, 4, "09:00", "10:00")])

    assert (
        str(refused.value)
        == f"{kyoto_stop.name} is in Kyoto; it can only move to a day in Kyoto"
    )


def test_a_move_inside_the_same_city_is_allowed() -> None:
    plan = make_two_city_plan()
    kyoto_stop = plan.days[0].items[0]

    moved = apply_layout(plan, [_move(kyoto_stop.id, 2, "11:00", "12:00")])

    assert moved.version == plan.version + 1
    assert kyoto_stop.id in [item.id for item in moved.days[1].items]


@pytest.mark.parametrize("kind", [ItemType.TRANSIT, ItemType.TICKET, ItemType.HOTEL])
def test_a_travel_ticket_or_hotel_row_cannot_be_moved_or_retimed(
    kind: ItemType,
) -> None:
    plan = make_plan(days=2, with_tickets=False)
    row = plan.days[0].items[1]
    row.type = kind

    with pytest.raises(ValueError) as refused:
        apply_layout(plan, [_move(row.id, 2, "15:00", "16:00")])
    with pytest.raises(ValueError):
        apply_layout(plan, [_move(row.id, 1, "15:00", "16:00")])

    assert row.name in str(refused.value)


def test_a_row_that_is_posted_unchanged_is_not_a_move() -> None:
    plan = make_plan(days=1, with_tickets=False)
    row = plan.days[0].items[1]
    row.type = ItemType.TRANSIT

    assert apply_layout(plan, [_move(row.id, 1, row.start, row.end)]) is plan


def test_a_locked_stop_still_cannot_be_moved() -> None:
    plan = make_plan(days=2, with_tickets=False)
    item = plan.days[1].items[0]
    item.locked = True

    with pytest.raises(ValueError, match="locked"):
        apply_layout(plan, [_move(item.id, 1, "09:00", "09:50")])


# -- through the route -------------------------------------------------------------------------------------------------


class Stored:
    def __init__(self, client: TestClient, session_id: str) -> None:
        self.client, self.session_id = client, session_id
        self.store = client.app.state.container.store

    def post(self, items: list[dict], **more):
        return self.client.post(
            f"/api/journeys/{self.session_id}/layout",
            json={"items": items, **more},
            headers=GUEST,
        )

    @property
    def plan(self) -> TripPlan:
        return self.store.get_plan(self.session_id)


@pytest.fixture()
def stored(settings: Settings):
    with TestClient(create_app(settings)) as client:
        session_id = client.post("/api/journeys", json={"client_token": "test"}).json()[
            "session"
        ]["id"]
        store = client.app.state.container.store
        plan = make_plan(days=2, with_tickets=False).model_copy(
            update={"session_id": session_id}
        )
        for number, day in enumerate(plan.days):
            for position, item in enumerate(day.items):
                item.geo = GeoPoint(
                    lat=35.0 + 0.01 * position, lng=135.7 + 0.01 * number
                )
        store.save_plan(plan, "First plan")
        store.save_context(TripContext(session_id=session_id, destination="Kyoto"))
        yield Stored(client, session_id)


def test_an_overlap_through_the_route_is_422_with_the_sentence_and_nothing_is_saved(
    stored: Stored,
) -> None:
    before = stored.plan
    first, lunch = before.days[0].items[0], before.days[0].items[1]

    response = stored.post(
        [_move(lunch.id, 1, "09:20", "10:20")], plan_version=before.version
    )

    assert response.status_code == 422
    assert (
        response.json()["detail"]
        == f"{first.name} and {lunch.name} would overlap on {before.days[0].date}"
    )
    assert stored.plan.version == before.version


def test_a_valid_retime_makes_a_new_version_and_works_the_transfer_around_it_out_again(
    stored: Stored,
) -> None:
    before = stored.plan
    lunch = before.days[0].items[1]
    assert lunch.transfer is None

    response = stored.post(
        [_move(lunch.id, 1, "12:00", "13:00")], plan_version=before.version
    )

    assert response.status_code == 200, response.text
    after = stored.plan
    moved = next(item for item in after.days[0].items if item.id == lunch.id)
    assert after.version == before.version + 1 and (moved.start, moved.end) == (
        "12:00",
        "13:00",
    )
    assert (
        moved.transfer is not None
        and moved.transfer.from_item_id == before.days[0].items[0].id
    )


def test_a_layout_made_on_an_earlier_version_is_409_and_changes_nothing(
    stored: Stored,
) -> None:
    before = stored.plan
    lunch = before.days[0].items[1]

    response = stored.post(
        [_move(lunch.id, 1, "12:00", "13:00")], plan_version=before.version + 3
    )

    assert response.status_code == 409
    assert stored.plan.version == before.version


def test_a_layout_with_no_plan_version_still_works(stored: Stored) -> None:
    lunch = stored.plan.days[0].items[1]

    assert stored.post([_move(lunch.id, 1, "12:00", "13:00")]).status_code == 200
