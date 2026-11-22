"""Views shared by the API routes: session state, calendar, map segments, translation."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.api.deps import Container
from app.domain.models import (
    ChatMessage,
    PlanTranslation,
    TripContext,
    TripPlan,
    VersionEntry,
    now_iso,
)
from app.evidence import atlas
from app.evidence.providers import haversine_km

MODE_SPEED = {"walk": 4.5, "cycle": 14.0, "transit": 18.0, "drive": 32.0}


async def session_view(c: Container, session_id: str, session: dict[str, Any]) -> dict[str, Any]:
    context = await load_context(c, session_id)
    plan_row = await c.store.load_plan(session_id)
    plan = TripPlan(**plan_row) if plan_row else None
    versions = await c.store.list_versions(session_id)
    messages = await c.store.list_messages(session_id)
    seat = await c.store.seat_holder(session_id)
    return {
        "session": {"id": session["id"], "created_at": session["created_at"]},
        "context": context.model_dump(),
        "plan": plan.model_dump(mode="json") if plan else None,
        "versions": versions,
        "messages": messages,
        "held": bool(session.get("held")),
        "agent_name": session.get("agent_name") or "",
        "planner_busy": bool(seat),
        "planner_note": session.get("note") or "",
    }


async def load_context(c: Container, session_id: str) -> TripContext:
    row = await c.store.load_context(session_id)
    if row:
        row = dict(row)
        for key in ("session_id", "destination", "origin", "hotel_style"):
            if row.get(key) is None:
                row[key] = ""
        return TripContext(**row)
    return TripContext(session_id=session_id, currency=c.settings.default_currency)


async def load_plan(c: Container, session_id: str, version: int | None = None) -> TripPlan | None:
    row = await c.store.load_plan(session_id, version)
    return TripPlan(**row) if row else None


async def next_version(c: Container, session_id: str) -> int:
    versions = await c.store.list_versions(session_id)
    return (versions[0]["version"] + 1) if versions else 1


async def save_plan(c: Container, plan: TripPlan, change_summary: str) -> TripPlan:
    plan.version = await next_version(c, plan.session_id)
    plan.change_summary = change_summary[:300]
    plan.updated_at = now_iso()
    await c.store.save_plan(plan.model_dump(mode="json"))
    return plan


def fingerprint(name: str) -> str:
    return hashlib.sha1(name.lower().encode("utf-8")).hexdigest()[:10]


# -- map -----------------------------------------------------------------


def segments_payload(plan: TripPlan, day: str | None = None) -> dict[str, Any]:
    legs: list[dict[str, Any]] = []
    anchors: dict[str, dict[str, Any]] = {}
    for day_plan in plan.days:
        if day and day_plan.date != day:
            continue
        for index, item in enumerate(day_plan.items):
            if item.geo:
                anchors[item.id] = {"id": item.id, "name": item.name, "lat": item.geo.lat, "lng": item.geo.lng}
            if index == 0 or not item.geo:
                continue
            previous = day_plan.items[index - 1]
            if not previous.geo:
                continue
            km = haversine_km(previous.geo, item.geo)
            mode = item.transfer.mode if item.transfer else ("walk" if km <= 1.4 else "transit")
            minutes = item.transfer.minutes if item.transfer else round(km * 1.3 / MODE_SPEED.get(mode, 4.5) * 60, 1)
            legs.append(
                {
                    "from_id": previous.id,
                    "to_id": item.id,
                    "mode": mode,
                    "minutes": minutes,
                    "km": round(km, 2),
                    "path": [[previous.geo.lat, previous.geo.lng], [item.geo.lat, item.geo.lng]],
                    "source": item.transfer.source if item.transfer else "estimate",
                }
            )
    return {"legs": legs, "anchors": list(anchors.values())}


def leg_options(plan: TripPlan, from_id: str, to_id: str) -> dict[str, Any]:
    items = plan.item_index()
    origin, target = items.get(from_id), items.get(to_id)
    if origin is None or target is None or not origin.geo or not target.geo:
        return {"from_id": from_id, "to_id": to_id, "from_name": "", "to_name": "", "options": []}
    km = haversine_km(origin.geo, target.geo)
    options = []
    for mode in ("walk", "cycle", "transit", "drive"):
        minutes = round(km * 1.3 / MODE_SPEED[mode] * 60, 1)
        options.append(
            {
                "mode": mode,
                "minutes": minutes,
                "km": round(km * 1.3, 2),
                "source": "estimate",
                "routed": False,
                "path": [[origin.geo.lat, origin.geo.lng], [target.geo.lat, target.geo.lng]],
            }
        )
    return {
        "from_id": from_id,
        "to_id": to_id,
        "from_name": origin.name,
        "to_name": target.name,
        "options": options,
    }


# -- calendar ------------------------------------------------------------


def calendar_ics(plan: TripPlan) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def escape(text: str) -> str:
        return (text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Pathfinder//v5//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape(plan.destination.name)} trip",
    ]
    for day in plan.days:
        for item in day.items:
            start = _stamp(day.date, item.start)
            end = _stamp(day.date, item.end)
            lines.extend(
                [
                    "BEGIN:VEVENT",
                    f"UID:{item.id}@pathfinder",
                    f"DTSTAMP:{stamp}",
                    f"DTSTART:{start}",
                    f"DTEND:{end}",
                    f"SUMMARY:{escape(item.name)}",
                    f"LOCATION:{escape(item.address)}",
                    f"DESCRIPTION:{escape((item.notes or '')[:900])}",
                    "END:VEVENT",
                ]
            )
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _stamp(day: str, clock: str) -> str:
    base = day.replace("-", "")
    parts = (clock or "09:00").split(":")
    hour = parts[0].zfill(2) if parts else "09"
    minute = parts[1].zfill(2) if len(parts) > 1 else "00"
    return f"{base}T{hour}{minute}00"


# -- translation ---------------------------------------------------------


async def translate_plan(c: Container, plan: TripPlan, locale: str) -> dict[str, Any]:
    if locale == "en":
        return PlanTranslation(
            locale="en", plan_version=plan.version, status="english", reason="", disclaimer="", fields={}
        ).model_dump()
    cache_key = f"translation:{plan.session_id}:{plan.version}:{locale}"
    cached = await c.store.get_kv(cache_key)
    if cached:
        return cached
    from app.core.llm import LLMClient, LLMError
    from app.core.prompts import skill
    from app.understand.schemas import TranslationDraft

    payload = {
        "summary": plan.summary,
        "days": {day.date: {"title": day.title, "note": day.note} for day in plan.days},
        "items": {item.id: {"notes": item.notes} for day in plan.days for item in day.items if item.notes},
        "gaps": {str(index): gap.message for index, gap in enumerate(plan.data_quality.gaps)},
    }
    llm = LLMClient(c.settings)
    try:
        draft = await llm.json(
            purpose="translate",
            system=skill("translate"),
            user=json.dumps({"locale": locale, "fields": payload}, ensure_ascii=False),
            schema=TranslationDraft,
            max_tokens=4000,
        )
        status = "translated"
        fields = draft.model_dump(exclude_none=True)
        reason = ""
    except LLMError as exc:
        status = "unavailable"
        fields = {}
        reason = str(exc)[:200]
    finally:
        await llm.aclose()
    result = PlanTranslation(
        locale=locale,  # type: ignore[arg-type]
        plan_version=plan.version,
        status=status,  # type: ignore[arg-type]
        reason=reason,
        disclaimer="Translated by a model; the English plan is the source of truth.",
        fields=fields,
    ).model_dump()
    await c.store.set_kv(cache_key, result)
    return result


# -- runtime config ------------------------------------------------------


DEFAULT_FRESHNESS = {"weather": 6, "tickets": 24, "hotels": 24}
DEFAULT_THRESHOLDS = {"router": 0.8, "max_repairs": 2, "turn_deadline": 180}
from app.security.pii import CARD, EMAIL, HK_PHONE, HKID

DEFAULT_PATTERNS = {"hkid": HKID, "hk_phone": HK_PHONE, "email": EMAIL, "card": CARD}


async def runtime_config(c: Container) -> dict[str, Any]:
    settings = c.settings
    overrides = await c.store.get_kv("runtime") or {}
    writer_model = overrides.get("writer_model") or settings.writer[2]
    thresholds = {**DEFAULT_THRESHOLDS, **(overrides.get("thresholds") or {})}
    freshness = {**DEFAULT_FRESHNESS, **(overrides.get("freshness_hours") or {})}
    redaction = {**DEFAULT_PATTERNS, **(overrides.get("redaction") or {})}
    writer = overrides.get("writer") or {}
    data_mode = overrides.get("data_mode") or ("atlas" if settings.sandbox else "live")
    orchestration = overrides.get("orchestrator") or {}
    jev_override = orchestration.get("jev") or {}
    turn_mode = orchestration.get("mode") or settings.turn_orchestrator
    jev_model = jev_override.get("model") or settings.decision_model
    jev_url = jev_override.get("url") or settings.decision_api_url
    jev_key = jev_override.get("api_key") or settings.decision_api_key
    purpose_models = overrides.get("purpose_models") or {}
    base, _key, model = settings.writer
    return {
        "version": "5.0.0",
        "environment": settings.environment,
        "sandbox": data_mode == "atlas",
        "data_mode": data_mode,
        "thresholds": thresholds,

        "writer": {
            "model": writer.get("model") or writer_model,
            "temperature": writer.get("temperature", settings.llm_temperature),
            "max_tokens": writer.get("max_tokens", settings.llm_max_tokens),
            "reasoning": writer.get("reasoning", "none"),
            "available": bool(settings.writer[1]),
            "base_url": base,
        },
        "writer_model": writer_model,
        "models": {"default": writer_model, "purposes": purpose_models},
        "orchestrator": {
            "mode": turn_mode,
            "jev": {
                "model": jev_model,
                "url": jev_url,
                "key_set": bool(jev_key),
                "key_tail": _tail(jev_key),
            },
        },
        "poe": {"key_set": bool(settings.poe_api_key), "key_tail": _tail(settings.poe_api_key)},
        "redaction": {
            "hkid": bool(redaction.get("hkid", True)),
            "hk_phone": bool(redaction.get("hk_phone", True)),
            "email": bool(redaction.get("email", True)),
            "card": bool(redaction.get("card", True)),
            "patterns": {
                "hkid": redaction.get("hkid_pattern", DEFAULT_PATTERNS["hkid"]),
                "hk_phone": redaction.get("hk_phone_pattern", DEFAULT_PATTERNS["hk_phone"]),
                "email": redaction.get("email_pattern", DEFAULT_PATTERNS["email"]),
                "card": redaction.get("card_pattern", DEFAULT_PATTERNS["card"]),
            },
        },
        "providers": {
            "places": ["Google Places"],
            "weather": ["Open-Meteo"],
            "routes": ["OSRM", "estimate"],
            "flights": ["Google Flights (SerpAPI)", "estimate"],
            "search": ["SerpAPI", "Wikipedia"],
            "judge": ["DeepSeek"] if settings.judge_api_key else [],
        },
        "features": {
            "flights": settings.enable_flights,
            "routes": settings.enable_routes,
            "notes": settings.enable_notes,
            "youtube": settings.enable_youtube,
            "bilibili": settings.enable_bilibili,
        },
        "maps": {
            "google": {
                "key": settings.google_maps_api_key or settings.google_places_api_key,
                "enabled": bool(settings.google_maps_api_key or settings.google_places_api_key),
            }
        },
        "cities": len(atlas.CITIES),
        "capabilities": {
            "writer": True,
            "byok_keys": True,
            "thresholds": ["router", "max_repairs", "turn_deadline"],
            "privacy_masking": True,
            "serpapi_rotation": True,
            "freshness_hours": False,
            "jev": True,
            "purpose_models": True,
            "reviewer": False,
            "gate_arms": False,
            "model_arms": False,
        },
    }


def _tail(key: str) -> str:
    return ("••••" + key[-4:]) if key and len(key) > 4 else ""


def config_or_404() -> dict[str, Any]:
    return {}


async def wait_for(seconds: float) -> None:
    await asyncio.sleep(seconds)
