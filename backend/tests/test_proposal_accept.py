"""Accepting a held whole edit through the route: priced as a whole plan, checked against the plan as it stands now,
and given its airport transfers."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from plan_samples import make_two_city_plan
from test_airport_transfers import AirportGeocoder, FakeRoutes, _flights, _sync

from app.application.proposals import apply_change, proposal_changes
from app.config import Settings
from app.domain.models import Intent, Message, Money, TripContext, TripPlan
from app.interface.api import create_app

GUEST = {"X-Client-Token": "test"}


class Offered:
    """A client with a stored trip and a way to hold an edit of it, as the web does."""

    def __init__(self, client: TestClient, session_id: str, old: TripPlan) -> None:
        self.client, self.session_id, self.old = client, session_id, old
        self.store = client.app.state.container.store

    def hold(
        self, new: TripPlan, *, base_version: int | None = None
    ) -> tuple[str, str]:
        changes = proposal_changes(self.old, new, summary="Edited the trip.")
        assert [c["id"] for c in changes] == ["whole-edit"]
        proposal = {
            "changes": changes,
            "base_version": self.old.version if base_version is None else base_version,
        }
        message = Message(
            session_id=self.session_id,
            role="assistant",
            content="This edit is ready and is not written yet.",
            intent=Intent.MODIFY,
            meta={"proposal": proposal},
        )
        self.store.add_message(message)
        return message.id, changes[0]["id"]

    def decide(self, message_id: str, change_id: str, accept: bool = True):
        return self.client.post(
            f"/api/journeys/{self.session_id}/proposals/{message_id}",
            json={"change_id": change_id, "accept": accept},
            headers=GUEST,
        )

    @property
    def plan(self) -> TripPlan:
        return self.store.get_plan(self.session_id)


def _edited(old: TripPlan) -> TripPlan:
    new = old.model_copy(deep=True)
    new.party_size = old.party_size + 1
    new.hotels[1].name = "Osaka Garden Inn"
    new.hotels[1].price_per_night = Money(amount=3000, currency="HKD")
    return new


@pytest.fixture()
def offered(settings: Settings):
    with TestClient(create_app(settings)) as client:
        session_id = client.post("/api/journeys", json={"client_token": "test"}).json()[
            "session"
        ]["id"]
        container = client.app.state.container
        old = _flights(make_two_city_plan()).model_copy(
            update={"session_id": session_id}
        )
        container.store.save_plan(old, "First plan")
        container.store.save_context(
            TripContext(session_id=session_id, destination="Kyoto")
        )
        yield Offered(client, session_id, container.store.get_plan(session_id))


def test_an_accepted_edit_of_a_two_city_plan_is_priced_for_every_stay(
    offered: Offered,
) -> None:
    new = _edited(offered.old)
    message_id, change_id = offered.hold(new)
    container = offered.client.app.state.container
    planner = container.turn_service.modify_path.planner
    context = offered.store.get_context(offered.session_id)
    expected = planner.cost_for_plan(
        context, apply_change(offered.old, proposal_changes(offered.old, new)[0])
    )
    one_city = planner._cost(context, new.first_stay, new.tickets, new.dates)

    response = offered.decide(message_id, change_id)

    assert response.status_code == 200, response.text
    assert offered.plan.cost.total == expected.total
    assert expected.total != one_city.total


def test_a_failed_pricing_keeps_the_total_the_plan_had(
    offered: Offered, monkeypatch
) -> None:
    planner = offered.client.app.state.container.turn_service.modify_path.planner

    def broken(*_args, **_kwargs):
        raise ValueError("no price")

    monkeypatch.setattr(planner, "cost_for_plan", broken)
    before = offered.plan.cost.model_copy(deep=True)
    message_id, change_id = offered.hold(_edited(offered.old))

    response = offered.decide(message_id, change_id)

    assert response.status_code == 200, response.text
    assert offered.plan.cost == before


def test_an_edit_offered_on_an_earlier_version_is_refused_and_writes_nothing(
    offered: Offered,
) -> None:
    message_id, change_id = offered.hold(_edited(offered.old))
    later = offered.old.model_copy(deep=True)
    later.version = offered.old.version + 1
    later.party_size = 6
    offered.store.save_plan(later, "A later edit")

    response = offered.decide(message_id, change_id)

    assert response.status_code == 409
    assert (
        response.json()["detail"]
        == "The plan changed since this was offered; ask again."
    )
    assert offered.plan.version == later.version and offered.plan.party_size == 6


def test_a_stay_locked_after_the_offer_cannot_be_changed_by_accepting_it(
    offered: Offered,
) -> None:
    message_id, change_id = offered.hold(_edited(offered.old))
    locked = offered.client.post(
        f"/api/journeys/{offered.session_id}/lock",
        json={"kind": "hotel", "target_id": "hotel_b", "locked": True},
        headers=GUEST,
    )
    assert locked.status_code == 200, locked.text
    version = offered.plan.version

    response = offered.decide(message_id, change_id)

    assert response.status_code == 409
    assert (
        response.json()["detail"]
        == "This would change Osaka Inn, which is locked in. Press Change on it first."
    )
    assert (
        offered.plan.version == version and offered.plan.hotels[1].name == "Osaka Inn"
    )


def test_an_edit_that_replaces_a_stay_locked_before_the_offer_is_refused(
    offered: Offered,
) -> None:
    from app.application.choices import set_lock

    locked = offered.plan
    set_lock(locked, "hotel", "hotel_b", True)
    offered.store.save_plan(locked, None, replace=True)
    offered.old = offered.plan
    message_id, change_id = offered.hold(_edited(offered.old))

    response = offered.decide(message_id, change_id)

    assert response.status_code == 409
    assert "Osaka Inn" in response.json()["detail"]
    assert offered.plan.hotels[1].name == "Osaka Inn"


def test_an_edit_that_changes_the_option_chosen_on_a_locked_leg_is_refused(
    offered: Offered,
) -> None:
    from app.application.choices import set_lock

    locked = offered.plan
    set_lock(locked, "transport", "leg_ab", True)
    offered.store.save_plan(locked, None, replace=True)
    offered.old = offered.plan
    new = offered.old.model_copy(deep=True)
    for ticket in new.tickets:
        if ticket.leg_id == "leg_ab":
            ticket.selected = ticket.id == "t_ab_bus"
    message_id, change_id = offered.hold(new)

    response = offered.decide(message_id, change_id)

    assert response.status_code == 409
    assert (
        response.json()["detail"]
        == "This would change Kyoto → Osaka, which is locked in. Press Change on it first."
    )


def test_an_accepted_edit_that_has_flights_gets_its_two_airport_transfers(
    offered: Offered,
) -> None:
    container = offered.client.app.state.container
    container.registry.geocoder = AirportGeocoder()
    container.registry.routes = [FakeRoutes()]
    synced = offered.plan
    _sync(synced)
    synced.transfers_synced = True
    offered.store.save_plan(synced, None, replace=True)
    offered.old = offered.plan
    new = _edited(
        _flights(make_two_city_plan()).model_copy(
            update={"session_id": offered.session_id}
        )
    )
    new.version = offered.old.version
    assert not [leg for leg in new.legs if leg.kind in ("to_stay", "from_stay")]
    message_id, change_id = offered.hold(new)

    response = offered.decide(message_id, change_id)

    assert response.status_code == 200, response.text
    kinds = sorted(
        leg.kind for leg in offered.plan.legs if leg.kind in ("to_stay", "from_stay")
    )
    assert kinds == ["from_stay", "to_stay"]
