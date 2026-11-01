from app.application.orchestrator import _describe_diff
from app.application.validation import diff_plans
from tests.test_asking import _plan


def test_cost_change_is_part_of_the_diff(settings) -> None:
    _, _, _, _, _, _, plan = _plan(settings)
    old = plan.model_copy(deep=True)
    new = plan.model_copy(deep=True)
    new.cost.total = old.cost.total + 500
    new.cost.status = "over"

    changes = diff_plans(old, new)

    assert any(change["kind"] == "cost" for change in changes)
    text = _describe_diff(changes, new)
    assert "Total cost changed" in text
    assert "over" in text


def test_flight_change_is_part_of_the_diff(settings) -> None:
    _, _, _, _, _, _, plan = _plan(settings)
    old = plan.model_copy(deep=True)
    new = plan.model_copy(deep=True)
    assert new.tickets, "the sandbox plan should include a flight"
    new.tickets[0].name = "Cheaper Airline CA 999"

    changes = diff_plans(old, new)

    assert any(change["kind"] == "flight" for change in changes)
    assert "Flight changed" in _describe_diff(changes, new)
