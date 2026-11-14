"""Locking a chosen stay or leg: the model, the API (in place, no new version for a lock alone) and the refusals."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from plan_samples import make_two_city_plan

from app.config import Settings
from app.domain.models import HotelStay, TransportLeg, TripPlan
from app.interface.api import create_app

GUEST = {"X-Client-Token": "guest-lock"}


def _with_options(plan: TripPlan) -> TripPlan:
    """The two-city sample with a second Kyoto stay to choose from (Osaka has only the one)."""
    stay = plan.hotels[0]
    other = stay.model_copy(
        update={
            "id": "hotel_a2",
            "name": "Kyoto Garden Inn",
            "segment_id": stay.segment_id,
        }
    )
    plan.hotel_options = [stay.model_copy(), other]
    return plan


# --- the model ---------------------------------------------------------------------------------------------------------


def test_a_leg_is_not_locked_by_default_and_the_flag_survives_a_round_trip() -> None:
    leg = TransportLeg(id="l", from_place="A", to_place="B")
    assert leg.locked is False

    plan = _with_options(make_two_city_plan())
    plan.legs[1].locked = True
    plan.hotels[0].locked = True
    again = TripPlan.model_validate_json(plan.model_dump_json())

    assert again.legs[1].locked is True and again.hotels[0].locked is True


def test_a_plan_stored_without_the_flag_loads_with_every_slot_open() -> None:
    data = _with_options(make_two_city_plan()).model_dump(mode="json")
    for leg in data["legs"]:
        leg.pop("locked", None)

    plan = TripPlan.model_validate(data)

    assert all(leg.locked is False for leg in plan.legs)


def test_open_choices_are_the_unlocked_slots_with_two_or_more_options() -> None:
    plan = _with_options(make_two_city_plan())
    # leg_out has 2 options, leg_ab 2, leg_home 1; Kyoto has 2 stays, Osaka 1 (no options listed for it).
    assert plan.open_choices() == [
        ("hotel", "hotel_a"),
        ("transport", "leg_out"),
        ("transport", "leg_ab"),
    ]

    plan.hotels[0].locked = True
    plan.legs[1].locked = True

    assert plan.open_choices() == [("transport", "leg_out")]


def test_a_slot_with_one_option_or_none_is_never_open() -> None:
    plan = make_two_city_plan()  # no hotel options at all

    assert ("hotel", "hotel_a") not in plan.open_choices()
    assert ("transport", "leg_home") not in plan.open_choices()


# --- the API -----------------------------------------------------------------------------------------------------------


@pytest.fixture()
def client(settings: Settings):
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _saved(client: TestClient) -> tuple[str, TripPlan]:
    session_id = client.post(
        "/api/journeys", json={"client_token": "guest-lock"}
    ).json()["session"]["id"]
    plan = _with_options(make_two_city_plan())
    plan.session_id = session_id
    client.app.state.container.store.save_plan(plan, "test plan")
    return session_id, plan


def _latest(client: TestClient, session_id: str) -> TripPlan:
    return client.app.state.container.store.get_plan(session_id)


def _choose(client: TestClient, session_id: str, **body: Any):
    return client.post(
        f"/api/journeys/{session_id}/plan/choose", json=body, headers=GUEST
    )


def _lock(client: TestClient, session_id: str, **body: Any):
    return client.post(f"/api/journeys/{session_id}/lock", json=body, headers=GUEST)


def _stay(plan: TripPlan, stay_id: str) -> HotelStay:
    return next(s for s in plan.hotels if s.id == stay_id)


def test_choosing_a_stay_with_lock_makes_a_new_version_and_locks_it(
    client: TestClient,
) -> None:
    session_id, plan = _saved(client)

    answer = _choose(
        client,
        session_id,
        kind="hotel",
        target_id="hotel_a",
        option_id="hotel_a2",
        lock=True,
    )

    assert answer.status_code == 200, answer.text
    data = answer.json()["plan"]
    assert data["version"] == plan.version + 1
    stay = next(s for s in data["hotels"] if s["id"] == "hotel_a2")
    assert stay["locked"] is True
    assert _stay(_latest(client, session_id), "hotel_a2").locked is True


def test_choosing_the_current_option_with_lock_only_locks_without_a_new_version(
    client: TestClient,
) -> None:
    session_id, plan = _saved(client)

    answer = _choose(
        client,
        session_id,
        kind="hotel",
        target_id="hotel_a",
        option_id="hotel_a",
        lock=True,
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["plan"]["version"] == plan.version
    assert _latest(client, session_id).version == plan.version
    assert _stay(_latest(client, session_id), "hotel_a").locked is True


def test_choosing_a_leg_option_with_lock_locks_the_leg_and_selects_the_option(
    client: TestClient,
) -> None:
    session_id, plan = _saved(client)

    answer = _choose(
        client,
        session_id,
        kind="transport",
        target_id="leg_ab",
        option_id="t_ab_bus",
        lock=True,
    )

    assert answer.status_code == 200, answer.text
    latest = _latest(client, session_id)
    assert latest.version == plan.version + 1
    assert next(leg for leg in latest.legs if leg.id == "leg_ab").locked is True
    assert next(t for t in latest.tickets if t.id == "t_ab_bus").selected is True


def test_locking_the_chosen_leg_option_is_in_place(client: TestClient) -> None:
    session_id, plan = _saved(client)

    answer = _choose(
        client,
        session_id,
        kind="transport",
        target_id="leg_ab",
        option_id="t_ab_train",
        lock=True,
    )

    assert answer.status_code == 200, answer.text
    latest = _latest(client, session_id)
    assert latest.version == plan.version
    assert next(leg for leg in latest.legs if leg.id == "leg_ab").locked is True


def test_the_lock_route_locks_and_unlocks_in_place(client: TestClient) -> None:
    session_id, plan = _saved(client)

    locked = _lock(client, session_id, kind="hotel", target_id="hotel_a", locked=True)
    assert locked.status_code == 200, locked.text
    assert locked.json()["plan"]["version"] == plan.version
    assert _stay(_latest(client, session_id), "hotel_a").locked is True

    unlocked = _lock(
        client, session_id, kind="hotel", target_id="hotel_a", locked=False
    )
    assert unlocked.status_code == 200, unlocked.text
    assert _latest(client, session_id).version == plan.version
    assert _stay(_latest(client, session_id), "hotel_a").locked is False

    leg = _lock(client, session_id, kind="transport", target_id="leg_out", locked=True)
    assert leg.status_code == 200 and leg.json()["plan"]["version"] == plan.version
    assert (
        next(row for row in leg.json()["plan"]["legs"] if row["id"] == "leg_out")[
            "locked"
        ]
        is True
    )


def test_the_lock_route_refuses_an_unknown_target_and_an_older_version(
    client: TestClient,
) -> None:
    session_id, plan = _saved(client)

    unknown = _lock(
        client, session_id, kind="transport", target_id="leg_nowhere", locked=True
    )
    assert unknown.status_code == 404

    # A choice makes version 2; a lock aimed at version 1 is then refused.
    _choose(
        client, session_id, kind="transport", target_id="leg_ab", option_id="t_ab_bus"
    )
    old = _lock(
        client,
        session_id,
        kind="transport",
        target_id="leg_ab",
        locked=True,
        plan_version=plan.version,
    )
    assert old.status_code == 409


def test_choosing_on_a_locked_leg_is_refused_until_it_is_unlocked(
    client: TestClient,
) -> None:
    session_id, _ = _saved(client)
    _lock(client, session_id, kind="transport", target_id="leg_ab", locked=True)

    refused = _choose(
        client, session_id, kind="transport", target_id="leg_ab", option_id="t_ab_bus"
    )

    assert refused.status_code == 409
    assert "locked" in refused.json()["detail"].lower()

    _lock(client, session_id, kind="transport", target_id="leg_ab", locked=False)
    allowed = _choose(
        client, session_id, kind="transport", target_id="leg_ab", option_id="t_ab_bus"
    )
    assert allowed.status_code == 200, allowed.text


def test_choosing_on_a_locked_stay_is_refused_until_it_is_unlocked(
    client: TestClient,
) -> None:
    session_id, _ = _saved(client)
    _lock(client, session_id, kind="hotel", target_id="hotel_a", locked=True)

    refused = _choose(
        client, session_id, kind="hotel", target_id="hotel_a", option_id="hotel_a2"
    )
    assert refused.status_code == 409

    _lock(client, session_id, kind="hotel", target_id="hotel_a", locked=False)
    allowed = _choose(
        client, session_id, kind="hotel", target_id="hotel_a", option_id="hotel_a2"
    )
    assert allowed.status_code == 200, allowed.text


def test_a_lock_on_a_stop_is_not_a_thing(client: TestClient) -> None:
    session_id, _ = _saved(client)

    refused = _choose(
        client,
        session_id,
        kind="item",
        target_id="item_d1_s1",
        option_id="x",
        lock=True,
    )

    assert refused.status_code == 422
