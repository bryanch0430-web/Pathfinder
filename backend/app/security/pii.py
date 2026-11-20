"""Personal details a trip model does not need.

Hong Kong identity numbers, Hong Kong phone numbers, email addresses, and
payment cards can be masked. Spaces inside those values still match. An
overseas phone number is left as written. A card that fails its check digit
is left as written. A date written in the shape of an identity number is
masked too; that extra masking is deliberate.

The chat and the model copy use the person labels from the Redaction preview,
such as ``[HKID]`` and ``[CARD ****1111]``. The settings preview also builds a
stable machine token. The sample typed there is not stored.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_EMAIL_DEFAULT = r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"
_PHONE_DEFAULT = r"\+?\d[\d\s\-()]{6,}\d"
_CARD_DEFAULT = r"\b\d[\d -]{11,21}\d\b"
# Spaces may sit between the six digits: Y335776(5) and Y335 776(5).
_HKID_DEFAULT = r"(?<![A-Z0-9])[A-Z]{1,2}(?:\s*\d){6}(?!\d)(?:\s*\(?\s*[0-9A]\s*\)?)?"
DEFAULT_PATTERNS = {
    "hkid": _HKID_DEFAULT,
    "hk_phone": _PHONE_DEFAULT,
    "email": _EMAIL_DEFAULT,
    "card": _CARD_DEFAULT,
}
_PATTERN_LIMIT = 300
_DATE = re.compile(r"\d{4}[-\s]\d{1,2}[-\s]\d{1,2}")
_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


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
    def from_settings(cls, settings: object | None) -> "Redaction":
        if settings is None:
            return cls()
        return cls(
            hkid=bool(getattr(settings, "redact_hkid", True)),
            hk_phone=bool(getattr(settings, "redact_hk_phone", True)),
            email=bool(getattr(settings, "redact_email", True)),
            card=bool(getattr(settings, "redact_card", True)),
            hkid_pattern=str(getattr(settings, "redact_hkid_pattern", "") or ""),
            hk_phone_pattern=str(getattr(settings, "redact_hk_phone_pattern", "") or ""),
            email_pattern=str(getattr(settings, "redact_email_pattern", "") or ""),
            card_pattern=str(getattr(settings, "redact_card_pattern", "") or ""),
        )


def check_pattern(source: str) -> str:
    """Keep a blank pattern as the built-in one. A typed pattern must compile."""
    text = (source or "").strip()
    if not text:
        return ""
    if len(text) > _PATTERN_LIMIT:
        raise ValueError(f"A pattern can be at most {_PATTERN_LIMIT} characters.")
    try:
        re.compile(text)
    except re.error as exc:
        raise ValueError(f"That pattern is not a valid regular expression. {exc}") from exc
    return text


def _compiled(source: str, fallback: str) -> re.Pattern[str]:
    try:
        if source.strip():
            return re.compile(source)
    except re.error:
        pass
    return re.compile(fallback)


def _luhn(digits: str) -> bool:
    total = 0
    for index, char in enumerate(digits[::-1]):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0 and 13 <= len(digits) <= 19


def _is_hk_phone(raw: str) -> bool:
    if _DATE.fullmatch(raw.strip()):
        return False
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("00852"):
        local = digits[5:]
    elif digits.startswith("852") and len(digits) > 8:
        local = digits[3:]
    elif len(digits) == 8:
        local = digits
    else:
        return False
    return len(local) == 8 and local[0] in "23456789"


def _code(kind: str, raw: str) -> str:
    digest = hashlib.sha256(f"pathfinder|{kind}|{raw}".encode()).digest()
    number = int.from_bytes(digest[:5], "big")
    chars = []
    for _ in range(6):
        chars.append(_ALPHABET[number % 32])
        number //= 32
    return f"{kind}_{''.join(chars)}"


def _apply(text: str, redaction: Redaction, *, marks: bool, machine: bool) -> str:
    def email(match: re.Match[str]) -> str:
        if machine:
            return _code("EMAIL", match.group(0).casefold())
        return "[EMAIL]"

    def card(match: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", match.group(0))
        if not _luhn(digits):
            return match.group(0)
        if machine:
            return _code("CARD", digits)
        return f"[CARD ****{digits[-4:]}]"

    def phone(match: re.Match[str]) -> str:
        if not _is_hk_phone(match.group(0)):
            return match.group(0)
        if machine:
            return _code("HK_PHONE", re.sub(r"\D", "", match.group(0)))
        return "[PHONE]"

    def hkid(match: re.Match[str]) -> str:
        if machine:
            return _code("HKID", re.sub(r"\s", "", match.group(0)).upper())
        return "[HKID]"

    if redaction.email:
        text = _compiled(redaction.email_pattern, _EMAIL_DEFAULT).sub(email, text)
    if redaction.card:
        text = _compiled(redaction.card_pattern, _CARD_DEFAULT).sub(card, text)
    if redaction.hk_phone:
        text = _compiled(redaction.hk_phone_pattern, _PHONE_DEFAULT).sub(phone, text)
    if redaction.hkid:
        text = _compiled(redaction.hkid_pattern, _HKID_DEFAULT).sub(hkid, text)
    return text


def _current() -> Redaction:
    try:
        from app.config import get_settings

        return Redaction.from_settings(get_settings())
    except Exception:
        return Redaction()


def mask_pii(text: str, *, marks: bool = False, redaction: Redaction | None = None) -> str:
    """Mask the enabled patterns. ``marks`` is the chat transcript."""
    if not text:
        return text
    return _apply(text, redaction if redaction is not None else _current(), marks=marks, machine=False)


def preview_redaction(text: str, redaction: Redaction | None = None) -> dict[str, str]:
    """Person placeholders and machine tokens for the settings preview. Nothing is stored."""
    chosen = redaction or Redaction()
    return {
        "person": _apply(text, chosen, marks=False, machine=False),
        "machine": _apply(text, chosen, marks=False, machine=True),
    }
