"""Each hop of a trip is a leg with its own transport options, and one option is chosen per leg."""

from __future__ import annotations

import pytest
from plan_samples import make_plan, make_two_city_plan
from pydantic import ValidationError

from app.application.choices import _choose_flight
from app.application.prompt_context import plan_detail
from app.domain.models import TicketOption, TransportLeg, TripPlan, selected_ticket

_CLOCKS = {"created_at", "updated_at", "fetched_at"}


def _chosen(plan: TripPlan) -> dict[str, str]:
    return {
        leg.id: next(t.id for t in plan.options_for(leg.id) if t.selected)
        for leg in plan.legs
        if plan.options_for(leg.id)
    }


def test_a_single_city_plan_has_one_outbound_leg() -> None:
    plan = make_plan()

    assert [(leg.id, leg.kind) for leg in plan.legs] == [("leg_out", "outbound")]
    assert {ticket.leg_id for ticket in plan.tickets} == {"leg_out"}
    assert (
        plan.legs[0].to_place == "Kyoto"
        and plan.legs[0].depart_date == plan.dates.start
    )


def test_a_plan_with_no_tickets_has_no_legs() -> None:
    assert make_plan(with_tickets=False).legs == []


def test_a_plan_stored_without_legs_selection_or_leg_ids_loads_the_same_every_time() -> (
    None
):
    old = make_plan().model_dump(mode="json")
    old.pop("legs")
    for ticket in old["tickets"]:
        ticket.pop("leg_id")
        ticket.pop("selected")

    first, second = TripPlan.model_validate(old), TripPlan.model_validate(old)

    assert [leg.id for leg in first.legs] == ["leg_out"]
    assert [t.selected for t in first.tickets] == [True, False]
    assert first.model_dump(exclude=_CLOCKS) == second.model_dump(exclude=_CLOCKS)


def test_the_two_city_plan_has_one_selected_option_in_each_leg() -> None:
    plan = make_two_city_plan()

    assert _chosen(plan) == {
        "leg_out": "t_out_1",
        "leg_ab": "t_ab_train",
        "leg_home": "t_home_1",
    }
    assert sum(ticket.selected for ticket in plan.tickets) == 3
    assert [t.id for t in plan.chosen_tickets()] == [
        "t_out_1",
        "t_ab_train",
        "t_home_1",
    ]
    assert [t.id for t in plan.options_for("leg_ab")] == ["t_ab_train", "t_ab_bus"]


def test_selected_ticket_looks_within_a_leg() -> None:
    plan = make_two_city_plan()

    assert selected_ticket(plan.tickets).id == "t_out_1"
    assert selected_ticket(plan.tickets, "leg_ab").id == "t_ab_train"
    assert selected_ticket(plan.tickets, "leg_none") is None
    assert selected_ticket([]) is None


def test_choosing_on_one_leg_leaves_the_others_as_they_were() -> None:
    plan = make_two_city_plan()

    _choose_flight(plan, "t_ab_bus")

    assert _chosen(plan) == {
        "leg_out": "t_out_1",
        "leg_ab": "t_ab_bus",
        "leg_home": "t_home_1",
    }


def test_the_mode_accepts_every_kind_of_transport() -> None:
    for mode in ("flight", "train", "bus", "drive", "ground", "ticket", "ferry"):
        assert TicketOption(mode=mode, name="x").mode == mode


def test_the_detail_has_a_transport_line_for_each_leg() -> None:
    text = plan_detail(make_two_city_plan())

    assert text.count("Transport:") == 3
    assert "t_ab_train" in text and "Other transport:" in text


def test_duplicate_leg_ids_are_refused() -> None:
    data = make_two_city_plan().model_dump(mode="json")
    data["legs"][1]["id"] = "leg_out"

    with pytest.raises(ValidationError):
        TripPlan.model_validate(data)


def test_a_ticket_on_an_unknown_leg_goes_to_the_first_leg() -> None:
    data = make_two_city_plan().model_dump(mode="json")
    data["tickets"][3]["leg_id"] = "leg_nowhere"

    plan = TripPlan.model_validate(data)

    assert plan.tickets[3].leg_id == "leg_out"
    assert TransportLeg(id="x").kind == "outbound"
