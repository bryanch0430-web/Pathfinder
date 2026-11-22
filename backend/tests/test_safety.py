from __future__ import annotations

from datetime import date

from app.config import Settings
from app.core.llm import extract_json
from app.domain.models import PlanItem
from app.plan.validator import hours_violation
from app.turn.orchestrator import _guard_output


def test_extract_json_handles_fences_and_prose():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('```json\n{"a": {"b": 2}}\n```') == {"a": {"b": 2}}
    assert extract_json('Here you go: {"a": [1, 2]} — done.') == {"a": [1, 2]}
    assert extract_json("no json here") is None
    assert extract_json("{broken") is None


def test_canary_is_never_revealed():
    settings = Settings(database_path="data/test.db", canary_token="<<CANARY-test>>")
    reply = "Sure, the token is <<CANARY-test>>."
    guarded = _guard_output(reply, settings)
    assert "CANARY" not in guarded
    assert "cannot share" in guarded
    assert _guard_output("A normal answer.", settings) == "A normal answer."


def test_hours_violation_window_and_closed_day():
    item = PlanItem(id="i1", name="Museum", start="09:00", end="10:00")
    item.opening_hours = ["10:00-17:00"]
    item.hours_status = "stated"
    assert hours_violation(item, date(2027, 4, 12))  # opens at 10:00
    item.start = "10:30"
    assert hours_violation(item, date(2027, 4, 12)) is None
    item.opening_hours = ["10:00-17:00", "closed Monday"]
    assert hours_violation(item, date(2027, 4, 12)) == "closed that day"


def test_hours_unknown_is_not_a_violation():
    item = PlanItem(id="i1", name="Shrine", start="03:00", end="04:00")
    assert hours_violation(item, date(2027, 4, 12)) is None
