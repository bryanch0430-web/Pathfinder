"""A whole day for one place is a longer clock plus the other attractions taken off, held for a tick."""

from app.application.modification import exclusive_day_edit
from app.application.proposals import proposal_changes
from tests.plan_samples import make_plan


def test_whole_day_for_one_place_lengthens_it_and_drops_the_other_attractions() -> None:
    plan = make_plan(days=1, stops_per_day=2, with_tickets=False)
    park, other = plan.days[0].items[0], plan.days[0].items[1]
    park.name = "Ocean Park"
    park.opening_hours = ["Monday: 10:00 AM – 6:00 PM"]

    result = exclusive_day_edit(plan, "use whole day 1 for ocean park only, no other attractions")

    assert result is not None and result.hold
    updated, diff = result
    kept = {item.id: item for item in updated.days[0].items}
    assert (kept[park.id].start, kept[park.id].end) == ("10:00", "18:00")
    assert other.id not in kept
    assert any(item.name.startswith("Soba") for item in updated.days[0].items)
    assert diff
    changes = proposal_changes(plan, updated, summary=updated.change_summary)
    kinds = {change["id"].split("-", 1)[0] for change in changes}
    assert "time" in kinds or "move" in kinds
    assert "drop" in kinds


def test_a_plain_edit_is_not_a_whole_day() -> None:
    plan = make_plan(days=1, with_tickets=False)
    assert exclusive_day_edit(plan, "add a museum on day 1") is None
