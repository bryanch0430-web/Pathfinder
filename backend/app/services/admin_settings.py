"""Settings an administrator can change without editing code or the secret file.

The process keeps a copy so a chat turn does not query on every message. Saving
writes the database and the copy together. Restart loads the database again.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.admin_setting import AdminSetting

PROMPTS: dict[str, str] = {
    "plan": "Instructions for the first full draft.",
    "revise": "Instructions when a saved plan is edited in place.",
    "answer": "Instructions when a question must not change the plan.",
    "copilot_reply": "Instructions for the day-by-day reply. It may only word the computed facts.",
    "understand": "Instructions that read a chat message into trip fields.",
}

_cache: dict[str, str] = {}


def clear_cache() -> None:
    _cache.clear()


async def load_cache(db: AsyncSession) -> None:
    rows = (await db.execute(select(AdminSetting))).scalars().all()
    _cache.clear()
    _cache.update({row.key: row.value for row in rows})


async def _write(db: AsyncSession, key: str, value: str | None) -> None:
    current = await db.get(AdminSetting, key)
    if value is None:
        if current is not None:
            await db.delete(current)
        _cache.pop(key, None)
    elif current is None:
        db.add(AdminSetting(key=key, value=value))
        _cache[key] = value
    else:
        current.value = value
        _cache[key] = value
    await db.commit()


def jev_enabled() -> bool:
    """Jev stays off unless an administrator turns it on. The default is off."""
    saved = _cache.get("decision_enabled")
    if saved is None:
        return bool(settings.DECISION_ENABLED)
    return saved == "true"


def router_enabled() -> bool:
    """The chat gate stays off unless an administrator turns it on."""
    saved = _cache.get("router_enabled")
    if saved is None:
        return bool(settings.ROUTER_ENABLED)
    return saved == "true"


def router_view() -> dict:
    threshold = _cache.get("router_threshold")
    rules = _cache.get("rules_first")
    jev_on = bool(
        jev_enabled()
        and settings.DECISION_API_URL.strip()
        and settings.DECISION_API_KEY.strip()
    )
    return {
        "router_threshold": float(threshold) if threshold else settings.ROUTER_THRESHOLD,
        "rules_first": settings.ROUTER_RULES_FIRST if rules is None else rules == "true",
        "threshold_default": settings.ROUTER_THRESHOLD,
        "rules_first_default": settings.ROUTER_RULES_FIRST,
        "jev_configured": jev_on,
        "jev_enabled": jev_enabled(),
        "jev_key_set": bool(settings.DECISION_API_URL.strip() and settings.DECISION_API_KEY.strip()),
        "router_enabled": router_enabled(),
    }


def router_controls() -> tuple[float, bool]:
    view = router_view()
    return float(view["router_threshold"]), bool(view["rules_first"])


async def save_router(
    db: AsyncSession,
    *,
    threshold: float | None,
    rules_first: bool | None,
    decision_enabled: bool | None = None,
    router_enabled: bool | None = None,
) -> dict:
    if threshold is not None:
        await _write(db, "router_threshold", f"{threshold:.3f}")
    if rules_first is not None:
        await _write(db, "rules_first", "true" if rules_first else "false")
    if decision_enabled is not None:
        await _write(db, "decision_enabled", "true" if decision_enabled else "false")
    if router_enabled is not None:
        await _write(db, "router_enabled", "true" if router_enabled else "false")
    return router_view()


def stored_redaction():
    """The privacy switches. A missing row keeps that kind masked."""
    from app.security.pii import Redaction

    def flag(key: str) -> bool:
        raw = _cache.get(key)
        return True if raw is None else raw == "1"

    def text(key: str) -> str:
        return _cache.get(key) or ""

    return Redaction(
        hkid=flag("redact_hkid"),
        hk_phone=flag("redact_hk_phone"),
        email=flag("redact_email"),
        card=flag("redact_card"),
        hkid_pattern=text("redact_hkid_pattern"),
        hk_phone_pattern=text("redact_hk_phone_pattern"),
        email_pattern=text("redact_email_pattern"),
        card_pattern=text("redact_card_pattern"),
    )


def privacy_view() -> dict:
    chosen = stored_redaction()
    return {
        "hkid": chosen.hkid,
        "hk_phone": chosen.hk_phone,
        "email": chosen.email,
        "card": chosen.card,
        "hkid_pattern": chosen.hkid_pattern,
        "hk_phone_pattern": chosen.hk_phone_pattern,
        "email_pattern": chosen.email_pattern,
        "card_pattern": chosen.card_pattern,
    }


async def save_privacy(db: AsyncSession, body: dict) -> dict:
    from app.security.pii import check_pattern

    pairs = (
        ("redact_hkid", "hkid", "redact_hkid_pattern", "hkid_pattern"),
        ("redact_hk_phone", "hk_phone", "redact_hk_phone_pattern", "hk_phone_pattern"),
        ("redact_email", "email", "redact_email_pattern", "email_pattern"),
        ("redact_card", "card", "redact_card_pattern", "card_pattern"),
    )
    cleaned = {pattern_name: check_pattern(str(body.get(pattern_name) or "")) for _, _, _, pattern_name in pairs}
    for flag_key, flag_name, pattern_key, pattern_name in pairs:
        await _write(db, flag_key, "1" if body.get(flag_name, True) else "0")
        await _write(db, pattern_key, cleaned[pattern_name])
    return privacy_view()


def active_instruction(name: str, default: str) -> str:
    saved = _cache.get(f"prompt:{name}")
    return saved if saved and saved.strip() else default


def prompt_rows(defaults: dict[str, str]) -> list[dict]:
    rows = []
    for name, description in PROMPTS.items():
        override = _cache.get(f"prompt:{name}") or ""
        rows.append({
            "name": name,
            "description": description,
            "default": defaults.get(name, ""),
            "override": override,
        })
    return rows


async def save_prompt(db: AsyncSession, name: str, body: str) -> None:
    if name not in PROMPTS:
        raise KeyError(name)
    await _write(db, f"prompt:{name}", body.strip())


async def reset_prompt(db: AsyncSession, name: str) -> None:
    if name not in PROMPTS:
        raise KeyError(name)
    await _write(db, f"prompt:{name}", None)
