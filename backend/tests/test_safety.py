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


def test_pii_is_masked_in_the_first_layer():
    from app.security.pii import mask_pii

    text = "Call me on 9123 4567 or +852 6123 4567, email hal@example.com, HKID Y335776(5)."
    masked = mask_pii(text)
    assert "9123 4567" not in masked
    assert "hal@example.com" not in masked
    assert "Y335776" not in masked
    assert mask_pii("my HKID is Y3447 886(5)") == "my HKID is [HKID]"
    assert "[PHONE]" in masked and "[EMAIL]" in masked and "[HKID]" in masked


def test_pii_leaves_dates_and_prices_alone():
    from app.security.pii import mask_pii

    text = "Trip 2026-11-30 to 2026-12-02, budget 9000 HKD."
    assert mask_pii(text) == text


def test_pii_card_uses_luhn_and_last_four():
    from app.security.pii import mask_pii

    assert "[CARD ****1111]" in mask_pii("card 4111 1111 1111 1111")
    # An invalid card is not labelled as a card (a phone-shaped run may still be masked).
    assert "[CARD" not in mask_pii("card 1234 5678 9012 3456")


def test_preference_merge_dedupes():
    from app.application.memory import merge_preferences

    merged, added = merge_preferences(["likes museums"], ["Likes museums", "vegetarian", "  "])
    assert merged == ["likes museums", "vegetarian"]
    assert added == ["vegetarian"]


def test_preference_merge_collapses_paraphrased_travel_memory():
    from app.application.memory import merge_preferences

    existing = [
        "Me atraen los paisajes que parecen de Marte, como el desierto y la roca roja.",
        "Me gusta alojarme en un hotel céntrico y emblemático, cerca de lo más importante.",
    ]
    incoming = [
        "Me atraen los paisajes que parecen de otro planeta, como los desiertos de roca roja.",
        "Prefiero alojarme en un hotel céntrico y emblemático.",
        "Prefiero un ritmo equilibrado, con unas 3 atracciones al día.",
    ]
    merged, added = merge_preferences(existing, incoming)
    assert len(merged) == 3
    assert added == ["Prefiero un ritmo equilibrado, con unas 3 atracciones al día."]


def test_safety_reply_has_human_resources():
    from app.turn.orchestrator import _safety_reply

    text = _safety_reply()
    assert "999" in text and "2389 2222" in text and "findahelpline.com" in text
    assert "itinerary" not in text.lower()
