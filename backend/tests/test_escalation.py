"""Handing a chat to a person is a policy, separate from the route examples."""

from app.application.escalation import should_escalate
from app.application.prompts import LANGUAGE_RULE, PromptLibrary
from app.config import Settings
from app.infrastructure.llm import OpenAICompatibleLLM
from app.infrastructure.secret_box import SecretBox
from app.infrastructure.store import SQLiteStore


def _policy(**overrides):
    base = {
        "enabled": True,
        "message": "I'll pass this chat to a person.",
        "frustration_at": None,
        "examples": [],
    }
    base.update(overrides)
    return base


def test_asking_for_a_person_escalates_and_a_trip_request_does_not() -> None:
    policy = _policy()
    assert should_escalate("Can I talk to a person?", 0, policy) == "asked_for_a_person"
    assert should_escalate("Plan a 3-day trip to Hong Kong from Singapore", 0, policy) is None
    assert should_escalate("hello, how do you do?", 80, policy) is None


def test_a_close_example_escalates_and_a_weak_overlap_does_not() -> None:
    policy = _policy(examples=[{"utterance": "this is taking forever get me someone"}])
    assert should_escalate("this is taking forever get me someone now", 0, policy) == "example"
    assert should_escalate("add a museum on day 2", 0, policy) is None


def test_heat_escalates_only_when_a_threshold_is_set() -> None:
    assert should_escalate("....fuck you", 80, _policy()) is None
    assert should_escalate("....fuck you", 80, _policy(frustration_at=60)) == "frustration"
    assert should_escalate("Can I talk to a person?", 0, _policy(enabled=False)) is None


def test_disliking_a_meal_is_not_handed_to_a_person() -> None:
    from app.application.escalation import queue_reason

    line = "suggest what else can i change? i don't like to eat here"
    assert should_escalate(line, 72, _policy()) is None
    assert queue_reason(line, hot=False, stuck=False) is None


def test_a_meal_change_stays_with_the_planner_when_the_wording_is_unhappy() -> None:
    from app.application.escalation import keeps_the_planner, queue_reason

    line = "change to something else ok? i don't want to eat this"
    assert keeps_the_planner(line)
    assert queue_reason(line, hot=True, stuck=False) is None
    assert queue_reason("fuck you", hot=True, stuck=False) == "hot"
    assert not keeps_the_planner("change the meal and talk to a person")
    assert should_escalate("change the meal and talk to a person", 0, _policy()) == "asked_for_a_person"


def test_an_admin_threshold_replaces_the_environment_and_a_reset_restores_it(settings: Settings, tmp_path) -> None:
    store = SQLiteStore(tmp_path / "policy.db")
    start = settings.router_threshold
    fresh = settings.weather_fresh_seconds
    store.apply_runtime(settings)
    store.save_runtime({"router_confidence": "0.5", "weather": "12"})
    store.apply_runtime(settings)
    assert settings.router_threshold == 0.5
    assert settings.weather_fresh_seconds == 12 * 3600
    store.clear_runtime()
    store.apply_runtime(settings)
    assert settings.router_threshold == start
    assert settings.weather_fresh_seconds == fresh


def test_the_gate_can_switch_to_fast_reasoning_and_a_reset_restores_jev(settings: Settings, tmp_path) -> None:
    store = SQLiteStore(tmp_path / "gate.db")
    store.apply_runtime(settings)
    assert settings.router_gate == "jev"
    store.save_runtime({"router_gate": "fast"})
    store.apply_runtime(settings)
    assert settings.router_gate == "fast"
    store.save_runtime({"router_gate": "nope"})
    store.apply_runtime(settings)
    assert settings.router_gate == "jev"
    store.save_runtime({"router_gate": "fast"})
    store.apply_runtime(settings)
    store.clear_runtime()
    store.apply_runtime(settings)
    assert settings.router_gate == "jev"


def test_the_orchestrator_model_and_key_apply_and_a_reset_restores_them(settings: Settings, tmp_path) -> None:
    start_model = settings.writer_model
    start_key = settings.decision_api_key
    start_url = settings.decision_api_url
    start_poe = settings.poe_api_key
    writer = OpenAICompatibleLLM(settings)
    store = SQLiteStore(tmp_path / "orchestrator.db")
    box = SecretBox(settings)
    store.apply_runtime(settings)
    store.save_runtime(
        {
            "writer_model": "writer-next",
            "decision_model": "jev-next",
            "decision_api_url": "https://jev.example.com",
            "gate_fast_model": "fast-next",
            "gate_fast_base_url": "https://fast.example.com/v1",
        }
    )
    store.save_gate_secret("jev", box.encrypt("sk-jev-key-1234"))
    store.save_gate_secret("fast", box.encrypt("sk-fast-key-1234"))
    store.save_gate_secret("poe", box.encrypt("sk-poe-key-1234"))
    store.save_runtime(
        {
            "gate_plain_provider": "openai",
            "gate_plain_temperature": "0.4",
            "gate_plain_max_tokens": "256",
            "gate_plain_reasoning": "none",
        }
    )
    store.save_gate_secret("plain", box.encrypt("sk-plain-key-1234"))
    store.apply_runtime(settings)
    store.apply_gate_secrets(settings, box)
    assert settings.writer_model == "writer-next"
    assert writer.name == "writer-next"
    assert settings.decision_model == "jev-next"
    assert settings.decision_api_url == "https://jev.example.com"
    assert settings.decision_api_key == "sk-jev-key-1234"
    assert settings.gate_fast_model == "fast-next"
    assert settings.gate_fast_api_key == "sk-fast-key-1234"
    assert settings.poe_api_key == "sk-poe-key-1234"
    assert settings.gate_plain_provider == "openai"
    assert settings.gate_plain_temperature == 0.4
    assert settings.gate_plain_max_tokens == 256
    assert settings.gate_plain_api_key == "sk-plain-key-1234"
    store.clear_runtime()
    store.apply_runtime(settings)
    store.apply_gate_secrets(settings, box)
    assert settings.writer_model == start_model
    assert writer.name == start_model
    assert settings.decision_api_key == start_key
    assert settings.decision_api_url == start_url
    assert settings.poe_api_key == start_poe
    assert settings.gate_plain_provider == "poe"
    assert settings.gate_plain_temperature == 0.0
    assert settings.gate_plain_api_key == ""


def test_an_admin_override_replaces_a_prompt_and_a_blank_one_does_not(settings: Settings) -> None:
    library = PromptLibrary(settings, overrides=lambda name: "Be brief." if name == "persona" else None)
    assert "Be brief." in library.system("ask")
    assert "untrusted data" in library.system("ask")
    plain = PromptLibrary(settings)
    assert LANGUAGE_RULE in plain.system("ask")
