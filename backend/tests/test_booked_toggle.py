"""Marking a locked stay or leg as booked: the model, the API (in place, no new version), the refusals, and that a booking
is never dropped silently by an unlock, an edit, a route change, a transfer rebuild or a plan file from elsewhere."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from plan_samples import make_two_city_plan
from test_airport_transfers import _flights, _sync

from app.application.choices import locked_conflict
from app.application.multi_edit import _carry_locks
from app.config import Settings
from app.domain.models import HotelStay, TransportLeg, TripPlan
from app.interface.api import create_app

GUEST = {"X-Client-Token": "guest-booked"}


def _with_options(plan: TripPlan) -> TripPlan:
    stay = plan.hotels[0]
    other = stay.model_copy(update={"id": "hotel_a2", "name": "Kyoto Garden Inn"})
    plan.hotel_options = [stay.model_copy(), other]
    return plan


# --- the model ---------------------------------------------------------------------------------------------------------


def test_a_stay_and_a_leg_are_not_booked_by_default_and_the_flag_survives_a_round_trip() -> (
    None
):
    assert TransportLeg(id="l", from_place="A", to_place="B").booked is False
    plan = make_two_city_plan()
    assert plan.hotels[0].booked is False

    plan.hotels[0].locked = plan.hotels[0].booked = True
    plan.legs[1].locked = plan.legs[1].booked = True
    again = TripPlan.model_validate_json(plan.model_dump_json())

    assert again.hotels[0].booked is True and again.legs[1].booked is True


def test_a_plan_stored_without_the_flag_loads_with_nothing_booked() -> None:
    data = make_two_city_plan().model_dump(mode="json")
    for row in [*data["legs"], *data["hotels"]]:
        row.pop("booked", None)

    plan = TripPlan.model_validate(data)

    assert not any(row.booked for row in [*plan.legs, *plan.hotels])


# --- the API -----------------------------------------------------------------------------------------------------------


@pytest.fixture()
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _saved(client: TestClient) -> tuple[str, TripPlan]:
    session_id = client.post(
        "/api/journeys", json={"client_token": "guest-booked"}
    ).json()["session"]["id"]
    plan = _with_options(make_two_city_plan())
    plan.session_id = session_id
    client.app.state.container.store.save_plan(plan, "test plan")
    return session_id, plan


def _latest(client: TestClient, session_id: str) -> TripPlan:
    return client.app.state.container.store.get_plan(session_id)


def _lock(client: TestClient, session_id: str, **body: Any):
    return client.post(f"/api/journeys/{session_id}/lock", json=body, headers=GUEST)


def _book(client: TestClient, session_id: str, **body: Any):
    return client.post(f"/api/journeys/{session_id}/booked", json=body, headers=GUEST)


def _stay(plan: TripPlan, stay_id: str) -> HotelStay:
    return next(s for s in plan.hotels if s.id == stay_id)


def _leg(plan: TripPlan, leg_id: str) -> TransportLeg:
    return next(leg for leg in plan.legs if leg.id == leg_id)


def test_a_stay_that_is_not_locked_cannot_be_marked_booked(client: TestClient) -> None:
    session_id, _ = _saved(client)

    refused = _book(client, session_id, kind="hotel", target_id="hotel_a", booked=True)

    assert refused.status_code == 422
    assert "lock" in refused.json()["detail"].lower()
    assert refused.json()["detail"].endswith(".")
    assert _stay(_latest(client, session_id), "hotel_a").booked is False


def test_a_leg_that_is_not_locked_cannot_be_marked_booked(client: TestClient) -> None:
    session_id, _ = _saved(client)

    refused = _book(
        client, session_id, kind="transport", target_id="leg_ab", booked=True
    )

    assert refused.status_code == 422
    assert "lock" in refused.json()["detail"].lower()


def test_a_locked_stay_is_marked_booked_and_back_in_place(client: TestClient) -> None:
    session_id, plan = _saved(client)
    _lock(client, session_id, kind="hotel", target_id="hotel_a", locked=True)

    booked = _book(client, session_id, kind="hotel", target_id="hotel_a", booked=True)

    assert booked.status_code == 200, booked.text
    assert booked.json()["plan"]["version"] == plan.version
    latest = _latest(client, session_id)
    assert latest.version == plan.version
    assert _stay(latest, "hotel_a").booked is True
    assert next(o for o in latest.hotel_options if o.id == "hotel_a").booked is True

    unbooked = _book(
        client, session_id, kind="hotel", target_id="hotel_a", booked=False
    )

    assert unbooked.status_code == 200, unbooked.text
    latest = _latest(client, session_id)
    assert latest.version == plan.version
    assert _stay(latest, "hotel_a").booked is False
    assert _stay(latest, "hotel_a").locked is True


def test_a_locked_leg_is_marked_booked_in_place(client: TestClient) -> None:
    session_id, plan = _saved(client)
    _lock(client, session_id, kind="transport", target_id="leg_ab", locked=True)

    booked = _book(
        client, session_id, kind="transport", target_id="leg_ab", booked=True
    )

    assert booked.status_code == 200, booked.text
    row = next(r for r in booked.json()["plan"]["legs"] if r["id"] == "leg_ab")
    assert row["booked"] is True and row["locked"] is True
    assert _latest(client, session_id).version == plan.version


def test_a_booked_stay_cannot_be_unlocked_until_it_is_marked_not_booked(
    client: TestClient,
) -> None:
    session_id, _ = _saved(client)
    _lock(client, session_id, kind="hotel", target_id="hotel_a", locked=True)
    _book(client, session_id, kind="hotel", target_id="hotel_a", booked=True)

    refused = _lock(client, session_id, kind="hotel", target_id="hotel_a", locked=False)

    assert refused.status_code == 409
    detail = refused.json()["detail"]
    assert "not booked first" in detail and detail.endswith(".")
    stay = _stay(_latest(client, session_id), "hotel_a")
    assert stay.locked is True and stay.booked is True

    _book(client, session_id, kind="hotel", target_id="hotel_a", booked=False)
    unlocked = _lock(
        client, session_id, kind="hotel", target_id="hotel_a", locked=False
    )
    assert unlocked.status_code == 200, unlocked.text
    assert _stay(_latest(client, session_id), "hotel_a").locked is False


def test_a_booked_leg_cannot_be_unlocked(client: TestClient) -> None:
    session_id, _ = _saved(client)
    _lock(client, session_id, kind="transport", target_id="leg_ab", locked=True)
    _book(client, session_id, kind="transport", target_id="leg_ab", booked=True)

    refused = _lock(
        client, session_id, kind="transport", target_id="leg_ab", locked=False
    )

    assert refused.status_code == 409
    assert "not booked first" in refused.json()["detail"]
    assert _leg(_latest(client, session_id), "leg_ab").locked is True


def test_locking_with_a_choice_again_keeps_the_booking(client: TestClient) -> None:
    session_id, _ = _saved(client)
    _lock(client, session_id, kind="hotel", target_id="hotel_a", locked=True)
    _book(client, session_id, kind="hotel", target_id="hotel_a", booked=True)

    again = client.post(
        f"/api/journeys/{session_id}/plan/choose",
        json={
            "kind": "hotel",
            "target_id": "hotel_a",
            "option_id": "hotel_a",
            "lock": True,
        },
        headers=GUEST,
    )

    assert again.status_code == 200, again.text
    assert _stay(_latest(client, session_id), "hotel_a").booked is True


def test_another_option_cannot_be_chosen_on_a_booked_stay(client: TestClient) -> None:
    session_id, _ = _saved(client)
    _lock(client, session_id, kind="hotel", target_id="hotel_a", locked=True)
    _book(client, session_id, kind="hotel", target_id="hotel_a", booked=True)

    refused = client.post(
        f"/api/journeys/{session_id}/plan/choose",
        json={"kind": "hotel", "target_id": "hotel_a", "option_id": "hotel_a2"},
        headers=GUEST,
    )

    assert refused.status_code == 409
    assert _stay(_latest(client, session_id), "hotel_a").booked is True


def test_the_booked_route_refuses_an_unknown_target_and_an_older_version(
    client: TestClient,
) -> None:
    session_id, plan = _saved(client)

    unknown = _book(
        client, session_id, kind="transport", target_id="leg_nowhere", booked=True
    )
    assert unknown.status_code == 404

    client.post(
        f"/api/journeys/{session_id}/plan/choose",
        json={"kind": "transport", "target_id": "leg_ab", "option_id": "t_ab_bus"},
        headers=GUEST,
    )
    _lock(client, session_id, kind="transport", target_id="leg_ab", locked=True)
    old = _book(
        client,
        session_id,
        kind="transport",
        target_id="leg_ab",
        booked=True,
        plan_version=plan.version,
    )
    assert old.status_code == 409
    assert _leg(_latest(client, session_id), "leg_ab").booked is False


# --- edits, route changes, transfers and imported files ----------------------------------------------------------------


def test_an_edit_that_changes_a_booked_stay_is_a_lock_conflict() -> None:
    before = make_two_city_plan()
    before.hotels[0].locked = before.hotels[0].booked = True
    after = before.model_copy(deep=True)
    after.hotels[0].name = "Somewhere Else"

    assert locked_conflict(before, after) == before.hotels[0].name


def test_a_route_change_keeps_the_booking_of_a_leg_and_stay_it_keeps() -> None:
    old = make_two_city_plan()
    old.legs[1].locked = old.legs[1].booked = True
    old.hotels[0].locked = old.hotels[0].booked = True
    new = make_two_city_plan()

    said = _carry_locks(old, new, {new.legs[1].id: []})

    assert said == []
    assert new.legs[1].locked is True and new.legs[1].booked is True
    assert new.hotels[0].locked is True and new.hotels[0].booked is True


def test_a_route_change_that_drops_a_booked_leg_says_the_booking_needs_the_provider() -> (
    None
):
    old = make_two_city_plan()
    old.legs[1].locked = old.legs[1].booked = True
    new = make_two_city_plan()
    new.legs[1].depart_date = None

    said = _carry_locks(old, new, {})

    assert len(said) == 1
    assert "booked" in said[0] and "Kyoto → Osaka" in said[0]


def test_an_unchanged_transfer_keeps_its_booking() -> None:
    plan = _flights(make_two_city_plan())
    _sync(plan)
    leg = next(l for l in plan.legs if l.id == "to_stay_seg_a")
    leg.locked = leg.booked = True

    _sync(plan)

    again = next(l for l in plan.legs if l.id == "to_stay_seg_a")
    assert again.locked is True and again.booked is True


def test_a_plan_file_from_elsewhere_opens_with_nothing_booked(
    client: TestClient,
) -> None:
    plan = make_two_city_plan()
    plan.hotels[0].locked = plan.hotels[0].booked = True
    plan.legs[1].locked = plan.legs[1].booked = True

    response = client.post(
        "/api/journeys/import",
        headers=GUEST,
        json={"client_token": "guest-booked", "plan": plan.model_dump(mode="json")},
    )

    assert response.status_code == 200, response.text
    imported = TripPlan.model_validate(response.json()["plan"])
    assert not any(row.booked for row in [*imported.hotels, *imported.hotel_options])
    assert not any(leg.booked for leg in imported.legs)
