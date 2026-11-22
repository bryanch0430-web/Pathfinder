"""Thirty ways of asking for a stop that is not a listed keyword.

Each case is add, then delete, then add again. The place that comes back does not
contain the words the traveller used. The script only strips the scaffolding;
the located row is what gets written.
"""

import pytest

from app.application.modification import (
    ChangeDraft,
    _add_attractions,
    _remove_attraction,
    _search_extra,
    asks_for_more_stops,
)
from app.application.plan_focus import day_number_named
from tests.plan_samples import make_plan

# message, a word that must survive in the search, the day it names
CASES = [
    ("ok sorry, can you add a stop at day 1 where i can see sakura flower", "sakura", 1),
    ("add somewhere to watch the sunset on day 2", "sunset", 2),
    ("include a place for night noodles on day 1", "noodles", 1),
    ("stick a museum on day 1", "museum", 1),
    ("throw in something with pandas on day 2", "pandas", 2),
    ("can u slot a hot spring on the first day", "spring", 1),
    ("add a stop to see the night view on day 1", "view", 1),
    ("please include a spot for street food on day 2", "food", 2),
    ("i wanna see robots, put it on day 1", "robots", 1),
    ("how about a garden on d ay 2", "garden", 2),
    ("add an atrction for lanterns on day 1", "lanterns", 1),
    ("find me a place to buy souvenirs on day 1", "souvenirs", 1),
    ("we should add a lookout on day 2", "lookout", 2),
    ("insert a stop where kids can play on day 1", "kids", 1),
    ("add on day 1 some place with a big ferris wheel", "ferris", 1),
    ("put a bookstore somewhere on day 2", "bookstore", 2),
    ("can you add a place to try beef noodles on day 1", "beef", 1),
    ("add one more thing, a temple, on day 1", "temple", 1),
    ("I'd like to squeeze in a cable car on day 2", "cable", 2),
    ("add a stop so we can see the harbour at night on day 1", "harbour", 1),
    ("include whatever is good for photos on day 1", "photos", 1),
    ("add a place that has a night market on day 2", "market", 2),
    ("day 1 please, somewhere quiet with trees", "trees", 1),
    ("add an atraction to see fireworks on day 2", "fireworks", 2),
    ("on the 2nd day add a stop for the old street", "street", 2),
    ("pls add smth like a shopping street on da y 1", "shopping", 1),
    ("add a stop where we can feed deer on day 1", "deer", 1),
    ("can you add a place to watch baseball on day 2", "baseball", 2),
    ("what about a rooftop on day 1", "rooftop", 1),
    ("let's put a teahouse on the third day", "teahouse", 3),
]


def _row(name: str, *, pin: bool) -> dict:
    row = {
        "id": name.lower().replace(" ", "-"),
        "name": name,
        "description": "Returned by the place search",
        "source_refs": [{"title": name, "url": "https://example.com/place", "source": "test"}],
    }
    if pin:
        row["geo"] = {"lat": 25.03, "lng": 121.56}
    return row


@pytest.mark.parametrize("message,token,day", CASES, ids=[str(i + 1) for i in range(len(CASES))])
def test_add_then_delete_then_add_again(message: str, token: str, day: int) -> None:
    assert asks_for_more_stops(message)
    assert day_number_named(message) == day
    query = _search_extra(message)["places_query"]
    assert token in query.lower()
    assert "day" not in query.lower()
    assert len(query) < len(message)

    plan = make_plan(days=3, with_tickets=False)
    rows = [_row("Untitled note", pin=False), _row("Pinned Overlook", pin=True)]
    assert _add_attractions(plan, rows, message) == ["Pinned Overlook"]
    assert any(item.name == "Pinned Overlook" for item in plan.days[day - 1].items)

    removed = _remove_attraction(plan, ChangeDraft(description=""), "delete Pinned Overlook")
    assert removed == "Pinned Overlook"
    assert all(item.name != "Pinned Overlook" for item in plan.days[day - 1].items)

    assert _add_attractions(plan, rows, message) == ["Pinned Overlook"]
    assert any(item.name == "Pinned Overlook" for item in plan.days[day - 1].items)
