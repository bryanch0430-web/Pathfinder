"""A whole day is a longer visit, not a search for a place with that name."""

from app.application.modification import _give_a_long_visit, _requested_alternative_text
from tests.plan_samples import make_plan, stop


def test_the_duration_words_are_not_a_place_name() -> None:
    message = "can you please day3 disneyland for a whole day, not just 2 hour"
    assert _requested_alternative_text(message) is None


def test_a_whole_day_lengthens_the_stop_and_moves_what_overlaps() -> None:
    plan = make_plan(days=1, with_tickets=False, stops_per_day=1)
    day = plan.days[0]
    park = stop("disney", "Hong Kong Disneyland", "17:00", "19:00")
    day.items.append(park)
    day.items.sort(key=lambda item: item.start)

    sentence, warnings = _give_a_long_visit(plan, day, park)

    assert park.start == "09:00"
    assert park.end == "18:30"
    assert "09:00–18:30" in sentence
    ordered = sorted(day.items, key=lambda item: item.start)
    for previous, current in zip(ordered, ordered[1:]):
        assert current.start >= previous.end, (previous.name, current.name)
    assert any("moved" in text for text in warnings)
