"""Bring-your-own key: the catalogue, the mask, and a round trip that never stores the raw key."""

from app.services.byok import PROVIDERS, decrypt_key, encrypt_key, mask_key, valid_key


def test_poe_defaults_to_claude_sonnet_and_xai_to_grok():
    assert PROVIDERS["poe"].default_model == "claude-sonnet-5-5"
    assert PROVIDERS["poe"].base_url == "https://api.poe.com/v1"
    assert PROVIDERS["xai"].label == "xAI Grok"
    assert PROVIDERS["xai"].default_model == "grok-4.7"
    assert "deepseek" in PROVIDERS


def test_mask_hides_the_middle():
    assert mask_key("sk-ant-secret-value") == "sk-…alue"
    assert not valid_key("short")
    assert valid_key("sk-ant-secret-value")


def test_sampling_accepts_temperature_and_effort():
    from app.services.byok import clean_sampling
    from app.services.llm.providers.byok_chat import ByokChatProvider

    assert clean_sampling("0.5", "high", "xai", "grok-4.7") == ("0.5", "high")
    assert clean_sampling("", "", "deepseek", "deepseek-flash") == (None, None)
    provider = ByokChatProvider(
        api_key="xai-test-key-value", base_url="https://api.x.ai/v1", model="grok-4.7",
        sampling_temperature="0.3", sampling_reasoning="none",
    )
    body = provider._responses_body("Plan one day", None, None)
    assert body["reasoning"] == {"effort": "high"}
    assert body["temperature"] == 0.3


def test_xhigh_is_only_offered_where_the_vendor_documents_it():
    from app.services.byok import clean_sampling, effort_options
    import pytest

    assert effort_options("xai", "grok-4.7") == ["low", "medium", "high", "xhigh"]
    assert "xhigh" in effort_options("poe", "claude-sonnet-5-5")
    assert "xhigh" not in effort_options("deepseek", "deepseek-flash")
    assert "xhigh" not in effort_options("gemini", "gemini-2.5-flash")
    assert effort_options("openai", "gpt-4o-mini") == []
    with pytest.raises(ValueError):
        clean_sampling("0.5", "xhigh", "deepseek", "deepseek-flash")


def test_grok_47_asks_for_high_reasoning():
    from app.services.llm.providers.byok_chat import ByokChatProvider

    provider = ByokChatProvider(api_key="xai-test-key-value", base_url="https://api.x.ai/v1", model="grok-4.7")
    body = provider._responses_body("Plan one day in Kyoto", "Be brief", None)
    assert body["model"] == "grok-4.7"
    assert body["reasoning"] == {"effort": "high"}
    assert body["input"][0]["role"] == "system"
    assert ByokChatProvider._response_text({"output_text": " Day one. "}) == "Day one."


def test_server_menu_uses_poe_when_that_writer_is_chosen(monkeypatch):
    from app.core.config import settings
    from app.services import byok

    monkeypatch.setattr(settings, "POE_API_KEY", "sk-poe-test-value")
    monkeypatch.setattr(settings, "POE_MODEL", "claude-sonnet-5-5")
    monkeypatch.setattr(settings, "SERVER_WRITER", "poe")
    from app.services.admin_settings import clear_cache
    clear_cache()
    view = byok.server_view()
    assert view["selected"] == "poe"
    assert view["effective"] == "poe"
    provider = byok.server_provider()
    assert provider is not None
    assert provider._model == "claude-sonnet-5-5"
    assert "poe" in provider._base_url


def test_ciphertext_round_trip_does_not_contain_the_key():
    secret = "sk-test-key-value"
    stored = encrypt_key(secret)
    assert secret not in stored
    assert decrypt_key(stored) == secret
