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


def test_a_total_in_another_currency_is_not_named_in_the_new_one(settings) -> None:
    """The cost entry of the diff carries the currency of each total: USD 590 became HKD 4,602, not HKD 590."""
    _, _, _, _, _, _, plan = _plan(settings)
    old = plan.model_copy(deep=True)
    new = plan.model_copy(deep=True)
    old.cost.currency, old.cost.total = "USD", 590.0
    new.cost.currency, new.cost.total = "HKD", 4602.0

    changes = diff_plans(old, new)

    entry = next(change for change in changes if change["kind"] == "cost")
    assert (entry["from_currency"], entry["currency"]) == ("USD", "HKD")
    assert f"Total cost changed from USD 590 to HKD 4,602 ({new.cost.status})." in _describe_diff(changes, new)


def test_a_total_in_the_same_currency_is_still_told_as_before(settings) -> None:
    _, _, _, _, _, _, plan = _plan(settings)
    old = plan.model_copy(deep=True)
    new = plan.model_copy(deep=True)
    old.cost.total, new.cost.total = 590.0, 530.0

    text = _describe_diff(diff_plans(old, new), new)

    assert f"Total cost changed from {old.cost.currency} 590 to {old.cost.currency} 530 (" in text


def test_a_cost_entry_without_the_old_currency_is_told_in_the_one_it_has(settings) -> None:
    """A diff stored before the entry carried both currencies."""
    _, _, _, _, _, _, plan = _plan(settings)
    entry = {"kind": "cost", "from": 590.0, "to": 530.0, "status": "within", "currency": "USD"}

    assert "Total cost changed from USD 590 to USD 530 (within)." in _describe_diff([entry], plan)
