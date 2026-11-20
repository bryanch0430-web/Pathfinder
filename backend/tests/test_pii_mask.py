"""Spaces inside a Hong Kong identity number or phone still mask. Overseas numbers stay."""

from app.application.prompt_context import _turn_line
from app.domain.models import Message
from app.security import fence_user_message, mask_pii
from app.security.pii import Redaction, preview_redaction

SAMPLE = (
    "Caller A123456(7) asked about policy PL1234567; "
    "reachable on 9123 4567 or chan@example.com, paid by card 4111 1111 1111 1111."
)


def test_the_sample_preview_uses_placeholders_and_tokens() -> None:
    shown = preview_redaction(SAMPLE)
    assert shown["person"] == (
        "Caller [HKID] asked about policy PL1234567; "
        "reachable on [PHONE] or [EMAIL], paid by card [CARD ****1111]."
    )
    assert "A123456" not in shown["machine"]
    assert "9123 4567" not in shown["machine"]
    assert "chan@example.com" not in shown["machine"]
    assert "4111" not in shown["machine"]
    assert "HKID_" in shown["machine"]
    assert "HK_PHONE_" in shown["machine"]
    assert "EMAIL_" in shown["machine"]
    assert "CARD_" in shown["machine"]
    assert shown["machine"] == preview_redaction(SAMPLE)["machine"]


def test_a_trip_request_keeps_the_plan_and_masks_the_unsolicited_details() -> None:
    text = (
        "Plan 4 days in Kyoto from Hong Kong for 2 people, leaving 5 December, budget 8000 HKD. "
        "My HKID is Y335 776(5). Call me on 9123 4567 or chan@example.com. The card is 4111 1111 1111 1111."
    )
    shown = preview_redaction(text)["person"]
    assert "Kyoto" in shown and "Hong Kong" in shown and "8000 HKD" in shown and "5 December" in shown
    assert "Y335" not in shown and "9123" not in shown and "chan@example.com" not in shown and "4111" not in shown
    assert "[HKID]" in shown and "[PHONE]" in shown and "[EMAIL]" in shown and "[CARD ****1111]" in shown


def test_spaces_between_identity_digits_still_match() -> None:
    text = "my HKID is Y335 776(5), and also Y335776(5)."
    masked = mask_pii(text, redaction=Redaction())
    assert "Y335" not in masked
    assert masked.count("[HKID]") == 2


def test_a_saved_pattern_replaces_the_built_in_one() -> None:
    masked = mask_pii("code ZZ123 and Y335776(5)", redaction=Redaction(hkid_pattern=r"ZZ\d{3}"))
    assert "[HKID]" in masked
    assert "ZZ123" not in masked
    assert "Y335776(5)" in masked


def test_spaces_inside_an_identity_number_and_a_phone_still_match() -> None:
    text = "HKID A 123456 (7), call +852 9123 4567, also 852-6097-5945."
    masked = mask_pii(text, redaction=Redaction())
    assert "123456" not in masked
    assert "9123" not in masked
    assert "6097" not in masked
    assert masked.count("[HKID]") == 1
    assert masked.count("[PHONE]") == 2


def test_an_overseas_number_a_date_and_a_budget_stay() -> None:
    text = "Call +1 202 555 0143 on 2026-12-05. Budget 4000 HKD. Meet at 09:30."
    assert mask_pii(text, redaction=Redaction()) == text


def test_a_card_that_fails_its_check_digit_stays() -> None:
    text = "Paid 4111 1111 1111 1112 in cash."
    assert mask_pii(text, redaction=Redaction()) == text


def test_a_switch_that_is_off_leaves_that_pattern() -> None:
    text = "Email ada@example.com and HKID A123456(7)."
    masked = mask_pii(text, redaction=Redaction(email=False))
    assert "ada@example.com" in masked
    assert "[HKID]" in masked


def test_the_transcript_uses_the_preview_labels() -> None:
    shown = mask_pii(
        "my HKID is Y335 776(5). Email ada@example.com. Card 4111 1111 1111 1111.",
        marks=True,
        redaction=Redaction(),
    )
    assert shown == "my HKID is [HKID]. Email [EMAIL]. Card [CARD ****1111]."


def test_the_model_fence_hides_the_email() -> None:
    fenced = fence_user_message("Plan Kyoto. Reach me at ada@example.com.")
    assert "ada@example.com" not in fenced
    assert "[EMAIL]" in fenced
    assert "Kyoto" in fenced


def test_an_earlier_turn_keeps_an_overseas_number() -> None:
    row = Message(session_id="s", role="user", content="My mobile is +1 202 555 0143 and the hotel is the Peninsula.")
    line = _turn_line(row, 240)
    assert "202 555 0143" in line
    assert "Peninsula" in line
