"""Speed and safety of the routing/compose changes: schema repair and fast parts."""

from __future__ import annotations

from types import SimpleNamespace

from app.core.llm import LLMClient
from app.understand.schemas import ParsedIntent, SlotUpdate


def _settings(**overrides):
    base = dict(
        writer=("https://writer.example/v1", "writer-key", "claude-sonnet-5.5"),
        purpose_models={},
        purpose_temperatures={},
        purpose_reasoning={},
        purpose_max_tokens={},
        verifier_base_url="https://api.deepseek.com/v1",
        verifier_api_key="deepseek-key",
        verifier_model="deepseek-flash",
        verifier_temperature=0.0,
        verifier_max_tokens=1500,
        verifier_timeout_seconds=60.0,
        llm_temperature=0.2,
        llm_max_tokens=4096,
        llm_reasoning="none",
        llm_timeout_seconds=60.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# -- the analyst schema accepts what models actually send --------------------


def test_stops_as_plain_strings_are_accepted():
    slots = SlotUpdate(stops=["Tokyo", "Kyoto"])
    assert [stop.city for stop in slots.stops] == ["Tokyo", "Kyoto"]


def test_stops_as_a_bare_string_are_accepted():
    slots = SlotUpdate(stops="Tokyo")
    assert [stop.city for stop in slots.stops] == ["Tokyo"]


def test_stops_already_in_object_form_are_kept():
    slots = SlotUpdate(stops=[{"city": "Osaka", "nights": 2}])
    assert slots.stops[0].city == "Osaka"
    assert slots.stops[0].nights == 2


def test_blank_and_wrong_stop_entries_are_dropped():
    slots = SlotUpdate(stops=["  ", 7, {"nights": 2}, {"city": "Nara"}])
    assert [stop.city for stop in slots.stops] == ["Nara"]


def test_a_whole_turn_answer_with_string_stops_validates():
    # The exact failure that caused the repair call: slots.stops.0 was a string.
    parsed = ParsedIntent.model_validate(
        {
            "intent": "modify",
            "confidence": 0.8,
            "slots": {"destination": "Hong Kong", "stops": ["Japan"]},
        }
    )
    assert parsed.slots.stops[0].city == "Japan"


# -- a light part uses the fast endpoint, the writer parts do not ------------


def test_edit_configured_as_deepseek_uses_the_fast_endpoint():
    client = LLMClient(_settings(purpose_models={"edit": "deepseek-flash"}))
    assert client.provider_for("edit") == ("https://api.deepseek.com/v1", "deepseek-key")
    assert client.model_for("edit") == "deepseek-flash"


def test_understand_without_a_configured_model_keeps_the_writer():
    client = LLMClient(_settings())
    assert client.provider_for("understand") == ("https://writer.example/v1", "writer-key")


def test_verify_keeps_its_existing_default_on_the_verifier_endpoint():
    client = LLMClient(_settings())
    assert client.provider_for("verify") == ("https://api.deepseek.com/v1", "deepseek-key")


def test_a_writer_model_named_for_a_light_part_keeps_the_writer():
    client = LLMClient(_settings(purpose_models={"edit": "claude-sonnet-5.5"}))
    assert client.provider_for("edit") == ("https://writer.example/v1", "writer-key")


def test_without_a_verifier_key_nothing_moves_off_the_writer():
    client = LLMClient(_settings(purpose_models={"edit": "deepseek-flash"}, verifier_api_key=""))
    assert client.provider_for("edit") == ("https://writer.example/v1", "writer-key")


# -- a fast part is not cut at the verifier's small cap ----------------------


def test_understand_keeps_its_own_cap_on_the_fast_endpoint():
    client = LLMClient(_settings(purpose_models={"understand": "deepseek-flash"}))
    assert client.max_tokens_for("understand") == 3000


def test_verify_keeps_the_verifier_cap():
    client = LLMClient(_settings())
    assert client.max_tokens_for("verify") == 1500


def test_plan_cap_is_lower_but_still_roomy():
    client = LLMClient(_settings())
    assert client.max_tokens_for("plan") == 8000


def test_an_explicit_purpose_cap_still_wins():
    client = LLMClient(_settings(purpose_max_tokens={"understand": 1200}))
    assert client.max_tokens_for("understand") == 1200


def test_a_light_part_takes_the_verifier_temperature():
    client = LLMClient(_settings(purpose_models={"edit": "deepseek-flash"}))
    assert client.temperature_for("edit") == 0.0
