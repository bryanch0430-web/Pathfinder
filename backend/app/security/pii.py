"""Personal details a trip model does not need.

This is the first layer: the traveller's text is masked **before** it is stored or
sent to any model, and the transcript and traces carry the masked copy. The same
patterns are configurable in Settings -> Privacy; a blank pattern keeps the built-in
one. An invalid custom pattern is ignored rather than breaking a turn.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

EMAIL = r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"
HK_PHONE = r"(?<!\d)(?:\+?852[\s-]?)?[2-9]\d{3}[\s-]?\d{4}(?!\d)"
CARD = r"\b\d[\d -]{11,21}\d\b"
HKID = r"(?<![A-Z0-9])[A-Z]{1,2}(?:\s*\d){6,7}(?!\d)(?:\s*\(?\s*[0-9A]\s*\)?)?"


@dataclass(frozen=True)
class Redaction:
    hkid: bool = True
    hk_phone: bool = True
    email: bool = True
    card: bool = True
    hkid_pattern: str = ""
    hk_phone_pattern: str = ""
    email_pattern: str = ""
    card_pattern: str = ""

    @classmethod
    def from_runtime(cls, overrides: Any) -> "Redaction":
        red = (overrides or {}).get("redaction") if isinstance(overrides, dict) else None
        if not isinstance(red, dict):
            return cls()
        patterns = red.get("patterns") if isinstance(red.get("patterns"), dict) else {}
        return cls(
            hkid=bool(red.get("hkid", True)),
            hk_phone=bool(red.get("hk_phone", True)),
            email=bool(red.get("email", True)),
            card=bool(red.get("card", True)),
            # A saved pattern from an older build can be narrower than the real format.
            # If it cannot match a known sample it would silently switch masking off, so
            # such a pattern falls back to the built-in one.
            hkid_pattern=_usable(red.get("hkid_pattern") or patterns.get("hkid"), HKID, "Y3447 886(5)"),
            hk_phone_pattern=_usable(red.get("hk_phone_pattern") or patterns.get("hk_phone"), HK_PHONE, "9123 4567"),
            email_pattern=_usable(red.get("email_pattern") or patterns.get("email"), EMAIL, "hal@example.com"),
            card_pattern=_usable(red.get("card_pattern") or patterns.get("card"), CARD, "4111 1111 1111 1111"),
        )


def _usable(source: Any, fallback: str, sample: str) -> str:
    text = str(source or "").strip()
    if not text:
        return ""
    try:
        pattern = re.compile(text)
    except re.error:
        return ""
    return "" if pattern.search(sample) else ""


def _compiled(source: str, fallback: str) -> re.Pattern[str]:
    text = (source or "").strip()
    if text and len(text) <= 300:
        try:
            return re.compile(text)
        except re.error:
            pass
    return re.compile(fallback)


def _luhn(number: str) -> bool:
    digits = [int(char) for char in number if char.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _mask_card(match: re.Match[str]) -> str:
    card = match.group(0)
    digits = "".join(char for char in card if char.isdigit())
    if not _luhn(digits):
        return card
    return f"[CARD ****{digits[-4:]}]"


def mask_pii(text: str, redaction: Redaction | None = None) -> str:
    """Replace the enabled personal details with stable person labels."""
    if not text:
        return text
    chosen = redaction or Redaction()
    value = text
    if chosen.email:
        value = _compiled(chosen.email_pattern, EMAIL).sub("[EMAIL]", value)
    # Cards first: a 16-digit card contains HK-phone-shaped runs.
    if chosen.card:
        value = _compiled(chosen.card_pattern, CARD).sub(_mask_card, value)
    if chosen.hk_phone:
        value = _compiled(chosen.hk_phone_pattern, HK_PHONE).sub("[PHONE]", value)
    if chosen.hkid:
        value = _compiled(chosen.hkid_pattern, HKID).sub("[HKID]", value)
    return value


def preview_pii(text: str, redaction: Redaction | None = None) -> dict[str, str]:
    chosen = redaction or Redaction()
    masked = mask_pii(text, chosen)
    return {
        "person": masked,
        "machine": masked.replace("[EMAIL]", "[EMAIL:redacted]").replace("[PHONE]", "[PHONE:redacted]"),
    }


__all__ = ["Redaction", "mask_pii", "preview_pii"]
