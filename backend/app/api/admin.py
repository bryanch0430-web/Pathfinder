"""Operator surfaces: runtime, prompts, escalation, the desk, review cases and redaction."""

from __future__ import annotations

import json
import math
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import container, require_admin, require_session, user_public
from app.api.views import load_context, load_plan, runtime_config, save_plan, session_view
from app.core.currencies import normalise_currency
from app.core.dates import shift_time
from app.core.prompts import SKILL_DIR, skill_names
from app.core.purposes import normalise_purpose_map
from app.domain.models import ChatRequest, TripPlan, now_iso
from app.plan.builder import refresh_derived

router = APIRouter()

from app.security.guard import severity as threat_severity
from app.security.pii import CARD, EMAIL, HK_PHONE, HKID

DEFAULT_PATTERNS = {
    "hkid": {"label": "HKID", "pattern": HKID},
    "hk_phone": {"label": "HK phone", "pattern": HK_PHONE},
    "email": {"label": "Email", "pattern": EMAIL},
    "card": {"label": "Card number", "pattern": CARD},
}

ESCALATION_DEFAULT = {
    "phrases_on": True,
    "enabled": False,
    "message": "A travel designer will join this chat.",
    "frustration_at": 0.75,
    "match_mode": "examples",
    "use_jev": False,
    "stuck_on": True,
    "stuck_after": 3,
    "words": ["useless", "terrible", "not working", "angry"],
    "human_phrases": ["live agent", "real person", "human agent", "talk to a person", "转人工", "真人客服"],
    "key_source": "none",
    "key_set": False,
    "key_saved": False,
    "key_tail": "",
    "examples": [],
    "parts": {},
}


@router.put("/api/admin/runtime")
async def save_runtime(request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    body = await request.json()
    stored = await c.store.get_kv("runtime") or {}
    for key in (
        "thresholds",
        "writer",
        "redaction",
        "writer_model",
        "data_mode",
        "purpose_models",
        "purpose_temperatures",
        "purpose_reasoning",
        "purpose_max_tokens",
    ):
        if key in body and body[key] is not None:
            stored[key] = body[key]
    for key in ("purpose_models", "purpose_temperatures", "purpose_reasoning", "purpose_max_tokens"):
        if isinstance(body.get(key), dict):
            stored[key] = normalise_purpose_map(body[key])
    if isinstance(body.get("purpose_temperatures"), dict):
        clean_temperatures = {}
        for key, value in normalise_purpose_map(body["purpose_temperatures"]).items():
            try:
                clean_temperatures[key] = max(0.0, min(2.0, float(value)))
            except (TypeError, ValueError):
                continue
        stored["purpose_temperatures"] = clean_temperatures
    if isinstance(body.get("purpose_max_tokens"), dict):
        clean_tokens = {}
        for key, value in normalise_purpose_map(body["purpose_max_tokens"]).items():
            try:
                clean_tokens[key] = max(16, min(32000, int(value)))
            except (TypeError, ValueError):
                continue
        stored["purpose_max_tokens"] = clean_tokens
    if isinstance(body.get("purpose_reasoning"), dict):
        stored["purpose_reasoning"] = {
            key: str(value).strip().lower()
            for key, value in normalise_purpose_map(body["purpose_reasoning"]).items()
            if str(value).strip().lower() in {"none", "low", "medium", "high"}
        }
    if isinstance(body.get("orchestrator"), dict):
        incoming = body["orchestrator"]
        current = stored.get("orchestrator") or {}
        jev = {**(current.get("jev") or {}), **(incoming.get("jev") or {})}
        if not jev.get("api_key"):
            jev.pop("api_key", None)
        if incoming.get("clear_jev_key"):
            jev.pop("api_key", None)
        mode = incoming.get("mode") or current.get("mode") or "analyst"
        stored["orchestrator"] = {"mode": mode if mode in {"analyst", "jev"} else "analyst", "jev": jev}
    # The Writer form saves temperature / max tokens / reasoning as writer_call.
    if isinstance(body.get("writer_call"), dict):
        call = {key: body["writer_call"][key] for key in ("temperature", "max_tokens", "reasoning") if key in body["writer_call"]}
        if call:
            stored["writer"] = {**(stored.get("writer") or {}), **call}
    if isinstance(body.get("poe"), dict):
        stored["poe"] = {key: value for key, value in body["poe"].items() if key == "api_key"}
    if isinstance(body.get("plain"), dict):
        stored["writer"] = {
            "model": body["plain"].get("model"),
            "temperature": body["plain"].get("temperature", 0.2),
            "max_tokens": body["plain"].get("max_tokens", 8192),
            "reasoning": body["plain"].get("reasoning", "none"),
        }
        stored["writer_model"] = body["plain"].get("model")
    await c.store.set_kv("runtime", stored)
    return await runtime_config(c)


@router.delete("/api/admin/runtime")
async def reset_runtime(request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    await c.store.delete_kv("runtime")
    return await runtime_config(c)


@router.post("/api/admin/redaction/preview")
async def redaction_preview(request: Request) -> dict[str, Any]:
    require_admin(request)
    body = await request.json()
    text = str(body.get("text", ""))
    redacted = text
    counts = {}
    for key, meta in DEFAULT_PATTERNS.items():
        if not body.get(key, True):
            continue
        pattern = body.get(f"{key}_pattern") or meta["pattern"]
        try:
            compiled = re.compile(pattern)
        except re.error:
            continue
        redacted, count = compiled.subn(f"[{meta['label']}]", redacted)
        counts[meta["label"]] = count
    machine = {key: value for key, value in counts.items() if value}
    return {"person": redacted, "machine": json.dumps(machine) if machine else "{}"}


@router.get("/api/admin/prompts")
async def get_prompts(request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    overrides = await c.store.get_kv("prompts") or {}
    rows = []
    for name in skill_names():
        path = SKILL_DIR / f"{name}.md"
        default = path.read_text(encoding="utf-8")
        rows.append(
            {
                "name": name,
                "description": _first_line(default),
                "default": default,
                "override": overrides.get(name, ""),
                "file": f"app/skills/{name}.md",
            }
        )
    return {"prompts": rows}


@router.put("/api/admin/prompts/{name}")
async def save_prompt(name: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    if name not in skill_names():
        raise HTTPException(status_code=404, detail="No such prompt.")
    body = await request.json()
    overrides = await c.store.get_kv("prompts") or {}
    overrides[name] = str(body.get("body", ""))
    await c.store.set_kv("prompts", overrides)
    return {"name": name}


@router.delete("/api/admin/prompts/{name}")
async def reset_prompt(name: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    overrides = await c.store.get_kv("prompts") or {}
    overrides.pop(name, None)
    await c.store.set_kv("prompts", overrides)
    return {"ok": True}


async def _json_object(request: Request) -> dict[str, Any]:
    """The request body as an object; a missing, malformed or non-object body is a 422."""
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - any parse failure is the caller's bad input
        raise HTTPException(status_code=422, detail="Send a JSON object as the request body.") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Send a JSON object as the request body.")
    return body


def _bad(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=message)


def _as_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    if value in (0, 1) and not isinstance(value, float):
        return bool(value)
    raise _bad(f"{name} must be true or false.")


def _as_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or value is None:
        raise _bad(f"{name} must be a number.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise _bad(f"{name} must be a number.") from None
    if not math.isfinite(number):
        raise _bad(f"{name} must be a number.")
    return number


def _as_int(value: Any, name: str, low: int, high: int) -> int:
    number = _as_number(value, name)
    if number != int(number) or not low <= int(number) <= high:
        raise _bad(f"{name} must be a whole number from {low} to {high}.")
    return int(number)


def _as_text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise _bad(f"{name} must be text.")
    return value.strip()[:limit]


def _as_texts(value: Any, name: str, limit: int, count: int) -> list[str]:
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        raise _bad(f"{name} must be a list of text.")
    return [item.strip()[:limit] for item in value if isinstance(item, str) and item.strip()][:count]


def _as_clock(value: Any, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,2}:\d{2}", value.strip()):
        raise _bad(f"{name} must be a time such as 09:30.")
    hours, minutes = (int(part) for part in value.strip().split(":"))
    if hours > 23 or minutes > 59:
        raise _bad(f"{name} must be a time such as 09:30.")
    return f"{hours:02d}:{minutes:02d}"


def _as_rows(body: dict[str, Any]) -> list[dict[str, Any]]:
    rows = body.get("items")
    if rows is None:
        return []
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise _bad("items must be a list of objects.")
    return rows


async def _require_chat(c, session_id: str) -> dict[str, Any]:
    session = await c.store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="That chat is not there.")
    return session


async def _note_operator_action(c, session_id: str, kind: str, text: str) -> None:
    """Operator instructions are kept apart from the traveller's transcript."""
    key = f"operator:log:{session_id}"
    rows = await c.store.get_kv(key) or []
    rows.append({"at": now_iso(), "kind": kind, "text": text[:500]})
    await c.store.set_kv(key, rows[-50:])


@router.get("/api/admin/escalation")
async def get_escalation(request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    stored = await c.store.get_kv("escalation") or {}
    policy = {**ESCALATION_DEFAULT, **stored}
    policy["parts"] = _monitor_parts(policy)
    return policy


@router.put("/api/admin/escalation")
async def save_escalation(request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    body = await _json_object(request)
    stored: dict[str, Any] = {}
    for key in ("phrases_on", "enabled", "stuck_on"):
        if key in body:
            stored[key] = _as_bool(body[key], key)
    if "message" in body and body["message"] is not None:
        stored["message"] = _as_text(body["message"], "message", 300)
    if body.get("frustration_at") not in (None, ""):
        value = _as_number(body["frustration_at"], "frustration_at")
        stored["frustration_at"] = max(0.0, min(1.0, value / 100.0 if value > 1 else value))
    if body.get("stuck_after") not in (None, ""):
        stored["stuck_after"] = max(2, min(8, int(_as_number(body["stuck_after"], "stuck_after"))))
    if "human_phrases" in body and body["human_phrases"] is not None:
        stored["human_phrases"] = _as_texts(body["human_phrases"], "human_phrases", 80, 20)
    await c.store.set_kv("escalation", {**ESCALATION_DEFAULT, **stored})
    policy = {**ESCALATION_DEFAULT, **stored}
    policy["parts"] = _monitor_parts(policy)
    return policy


def _monitor_parts(policy: dict[str, Any]) -> dict[str, Any]:
    def part(enabled: bool, engine: str, words: list[str], score: float | None, message: str, after: int) -> dict[str, Any]:
        return {
            "enabled": enabled,
            "engine": engine,
            "words": [{"text": word, "score": 0.0} for word in words[:20]],
            "examples": [],
            "score": score,
            "message": message,
            "after": after,
        }

    return {
        "hand": part(bool(policy.get("enabled")), policy.get("match_mode", "examples"), policy.get("words") or [], None, "Take the chat over.", 0),
        "sentiment": part(True, "model", [], 0.0, "Watch for frustration.", 1),
        "stuck": part(bool(policy.get("stuck_on")), "count", [], None, "The traveller is repeating themselves.", int(policy.get("stuck_after") or 3)),
    }


@router.get("/api/admin/threats")
async def threats(request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    counters = await c.store.get_kv("threats") or {}
    log = await c.store.get_kv("threat_log") or []
    events = []
    for category, count in counters.items():
        rows = [row for row in log if row.get("category") == category]
        sources: dict[str, int] = {}
        for row in rows:
            sources[row.get("source", "chat")] = sources.get(row.get("source", "chat"), 0) + 1
        events.append(
            {
                "category": category,
                "severity": threat_severity(category),
                "count": int(count),
                "sources": sources,
                "last_at": rows[-1].get("at", "") if rows else "",
                "last_session_id": rows[-1].get("session_id", "") if rows else "",
            }
        )
    recent = [
        {**row, "severity": threat_severity(row.get("category", ""))} for row in reversed(log[-100:])
    ]
    return {"events": events, "log": recent}


# -- the desk -----------------------------------------------------------


def _desk_contact(context) -> dict[str, Any]:
    return {
        "destination": context.destination,
        "origin": context.origin,
        "start": context.start_date or "",
        "end": context.end_date or "",
        "party": context.party_size,
        "budget": context.budget,
        "currency": context.currency,
        "preferences": context.preferences[:6],
    }


def _kv_number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _desk_state(finished: bool, recent: bool, held: bool) -> str:
    """Live while a chat is held for a person, even when its plan is already ready."""
    return "live" if held or (recent and not finished) else "completed"


def _desk_recent(value: str | None, hours: int = 24) -> bool:
    """A session is live while it changed in the last day; older idle chats belong in history."""
    if not value:
        return False
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - stamp < timedelta(hours=hours)
    except (TypeError, ValueError):
        return False


@router.get("/api/admin/desk")
async def desk_list(request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    sessions = await c.store.list_sessions(60)
    policy = {**ESCALATION_DEFAULT, **(await c.store.get_kv("escalation") or {})}
    stuck_after = int(policy.get("stuck_after") or 3)
    hot_at = float(policy.get("frustration_at") if policy.get("frustration_at") is not None else 0.75)
    chats = []
    for session in sessions:
        messages = await c.store.list_messages(session["id"], limit=50)
        if not messages:
            continue
        context = await load_context(c, session["id"])
        plan = await load_plan(c, session["id"])
        finished = bool(context.finalized) or (plan is not None and plan.status == "ready")
        recent = _desk_recent(session.get("updated_at"))
        state = _desk_state(finished, recent, bool(session.get("held")))
        last = messages[-1]
        stuck_count = _kv_number(await c.store.get_kv(f"stuck:{session['id']}"))
        frustration = max(0.0, min(1.0, _kv_number(await c.store.get_kv(f"frustration:{session['id']}"))))
        chats.append(
            {
                **_desk_contact(context),
                "session_id": session["id"],
                "created_at": session["created_at"],
                "updated_at": session["updated_at"],
                "state": state,
                "on_page": False,
                "finished": finished,
                "held": bool(session.get("held")),
                "agent_name": session.get("agent_name") or "",
                "stuck": bool(policy.get("stuck_on", True)) and stuck_count >= stuck_after,
                "frustration": frustration,
                "frustration_kind": "session" if frustration > 0 else "none",
                "band": "hot" if frustration >= hot_at else "uneasy" if frustration >= 0.4 else "calm",
                "sentiment_score": 0.0,
                "sentiment_label": "calm",
                "sentiment_notes": [],
                "preview": (last.get("content") or "")[:160],
                "last_role": last.get("role", ""),
            }
        )
    return {"chats": chats}


@router.get("/api/admin/desk/{session_id}")
async def desk_chat(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    session = await c.store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="That chat is not there.")
    context = await load_context(c, session_id)
    messages = await c.store.list_messages(session_id)
    return {
        "session_id": session_id,
        "held": bool(session.get("held")),
        "note": session.get("operator_note") or "",
        "contact": _desk_contact(context),
        "messages": [
            {
                "id": message["id"],
                "role": message["role"],
                "content": message["content"],
                "at": message["created_at"],
                "frustration": 0.0,
                "sentiment_label": "calm",
                "operator": bool((message.get("meta") or {}).get("operator")),
                "live_agent": bool((message.get("meta") or {}).get("live_agent")),
            }
            for message in messages
        ],
    }


@router.post("/api/admin/desk/{session_id}/hold")
async def desk_hold(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    await _require_chat(c, session_id)
    name = "Operator"
    from app.api.deps import bearer_user

    if bearer_user(request):
        user = await c.store.get_user(bearer_user(request))
        if user:
            name = user.get("display_name") or user.get("email") or name
    await c.store.set_held(session_id, True, name)
    return {"held": True, "agent_name": name}


@router.post("/api/admin/desk/{session_id}/release")
async def desk_release(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    await _require_chat(c, session_id)
    await c.store.set_held(session_id, False, "")
    return {"held": False}


@router.post("/api/admin/desk/{session_id}/note")
async def desk_note(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    await _require_chat(c, session_id)
    body = await _json_object(request)
    note = _as_text(body.get("note", ""), "note", 500) if body.get("note") is not None else ""
    await c.store.touch_session(session_id, operator_note=note)
    return {"note": note}


@router.post("/api/admin/desk/{session_id}/say")
async def desk_say(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    await _require_chat(c, session_id)
    body = await _json_object(request)
    text = _as_text(body.get("text"), "text", 2000)
    if not text:
        raise _bad("text must not be empty.")
    message_id = secrets.token_hex(8)
    await c.store.add_message(
        session_id,
        {
            "id": message_id,
            "role": "assistant",
            "content": text,
            "created_at": now_iso(),
            "meta": {"operator": True, "live_agent": True, "agent_name": "Operator"},
        },
    )
    return {"id": message_id}


@router.post("/api/admin/desk/{session_id}/run")
async def desk_run(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    await _require_chat(c, session_id)
    body = await _json_object(request)
    message = _as_text(body.get("message"), "message", 2000)
    if not message:
        raise _bad("message must not be empty.")
    from app.turn.orchestrator import Dependencies, collect_turn

    context = await load_context(c, session_id)
    await _note_operator_action(c, session_id, "run", message)
    events = await collect_turn(
        Dependencies(settings=c.settings, store=c.store, operator=True),
        ChatRequest(session_id=session_id, message=message, locale=context.language, review=False),
        session_id=session_id,
        user_message=message,
    )
    reply = "".join(event.data.get("text", "") for event in events if event.event == "token")
    intent = next((event.data.get("intent", "") for event in events if event.event == "done"), "")
    return {"reply": reply, "intent": intent, "plan": _desk_plan(await load_plan(c, session_id))}


@router.get("/api/admin/desk/{session_id}/itinerary")
async def desk_itinerary(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    plan = await load_plan(c, session_id)
    seat = await c.store.seat_holder(session_id)
    return {"plan": plan.model_dump(mode="json") if plan else None, "planner_busy": bool(seat)}


def _desk_plan(plan: TripPlan | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    return {
        "version": plan.version,
        "days": [
            {
                "date": day.date,
                "title": day.title,
                "items": [
                    {
                        "id": item.id,
                        "name": item.name,
                        "type": item.type,
                        "start": item.start,
                        "end": item.end,
                        "address": item.address,
                    }
                    for item in day.items
                ],
            }
            for day in plan.days
        ],
    }


@router.get("/api/admin/desk/{session_id}/plan")
async def desk_get_plan(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    return {"plan": _desk_plan(await load_plan(c, session_id))}


@router.post("/api/admin/desk/{session_id}/plan")
async def desk_save_plan(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    await _require_chat(c, session_id)
    body = await _json_object(request)
    rows = _as_rows(body)
    plan = await load_plan(c, session_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="There is no plan.")
    context = await load_context(c, session_id)
    index = plan.item_index()
    for row in rows:
        item = index.get(row.get("id"))
        if item is None:
            continue
        if row.get("remove"):
            for day in plan.days:
                day.items = [entry for entry in day.items if entry.id != item.id]
            continue
        item.start = _as_clock(row["start"], "start") if row.get("start") else item.start
        item.end = _as_clock(row["end"], "end") if row.get("end") else item.end
        if row.get("name"):
            item.name = str(row["name"])[:160]
    refresh_derived(plan, context)
    plan = await save_plan(c, plan, "Edited at the desk.")
    return {"plan": _desk_plan(plan)}


def _brief_date(body: dict[str, Any], key: str, current: str | None) -> date | None:
    value = body.get(key)
    if value in (None, ""):
        try:
            return date.fromisoformat(current[:10]) if current else None
        except ValueError:
            return None
    if not isinstance(value, str):
        raise _bad(f"{key} must be a date such as 2027-04-10.")
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise _bad(f"{key} must be a date such as 2027-04-10.") from None


@router.post("/api/admin/desk/{session_id}/brief")
async def desk_brief(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    await _require_chat(c, session_id)
    body = await _json_object(request)
    context = await load_context(c, session_id)
    updates: dict[str, Any] = {}
    for key in ("destination", "origin"):
        if body.get(key) not in (None, ""):
            updates[key] = _as_text(body[key], key, 120)
    if body.get("party_size") not in (None, ""):
        updates["party_size"] = _as_int(body["party_size"], "party_size", 1, 20)
    if body.get("budget") not in (None, ""):
        budget = _as_number(body["budget"], "budget")
        if budget < 0:
            raise _bad("budget must not be negative.")
        updates["budget"] = budget
    if body.get("currency") not in (None, ""):
        currency = normalise_currency(_as_text(body["currency"], "currency", 20))
        if not currency:
            raise _bad("currency must be a three-letter code such as HKD or JPY.")
        updates["currency"] = currency
    if body.get("preferences") is not None:
        if not isinstance(body["preferences"], list) or not all(isinstance(x, str) for x in body["preferences"]):
            raise _bad("preferences must be a list of text.")
        updates["preferences"] = _as_texts(body["preferences"], "preferences", 80, 20)
    start = _brief_date(body, "start_date", context.start_date)
    end = _brief_date(body, "end_date", context.end_date)
    if start and end and end < start:
        raise _bad("end_date must not be before start_date.")
    if body.get("start_date") not in (None, ""):
        updates["start_date"] = start.isoformat()
    if body.get("end_date") not in (None, ""):
        updates["end_date"] = end.isoformat()
    if start and end and ("start_date" in updates or "end_date" in updates):
        updates["days"] = (end - start).days + 1
    for key, value in updates.items():
        setattr(context, key, value)
    await c.store.save_context(session_id, context.model_dump())
    plan = await load_plan(c, session_id)
    return {
        "built": False,
        "reason": (
            "The brief was saved. The desk does not build an itinerary from it"
            + ("; the existing itinerary was not changed." if plan else ", so no plan exists yet.")
        ),
        "plan": _desk_plan(plan),
    }


@router.post("/api/admin/desk/{session_id}/layout")
async def desk_layout(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    await _require_chat(c, session_id)
    body = await _json_object(request)
    rows = _as_rows(body)
    plan = await load_plan(c, session_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="There is no plan.")
    context = await load_context(c, session_id)
    index = plan.item_index()
    for row in rows:
        item = index.get(row.get("id"))
        if item is None:
            continue
        day_index = _as_int(row.get("day") or 1, "day", 1, 366)
        if not 1 <= day_index <= len(plan.days):
            continue
        for day in plan.days:
            day.items = [entry for entry in day.items if entry.id != item.id]
        item.start = _as_clock(row["start"], "start") if row.get("start") else item.start
        item.end = _as_clock(row["end"], "end") if row.get("end") else item.end
        plan.days[day_index - 1].items.append(item)
    for day in plan.days:
        day.items.sort(key=lambda entry: entry.start or "99:99")
    refresh_derived(plan, context)
    plan = await save_plan(c, plan, "Rearranged at the desk.")
    return {"plan": plan.model_dump(mode="json")}


@router.get("/api/admin/desk/{session_id}/revisions")
async def desk_revisions(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    versions = await c.store.list_versions(session_id)
    return {"versions": [{"version": row["version"], "change_summary": row["change_summary"]} for row in versions]}


@router.post("/api/admin/desk/{session_id}/undo")
async def desk_undo(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    await _require_chat(c, session_id)
    body = await _json_object(request)
    version = _as_int(body.get("version"), "version", 1, 1_000_000)
    plan = await load_plan(c, session_id, version)
    if plan is None:
        raise HTTPException(status_code=404, detail="That version is not there.")
    plan = await save_plan(c, plan, f"Restored version {version}.")
    return {"plan": plan.model_dump(mode="json")}


@router.post("/api/admin/desk/{session_id}/edit")
async def desk_edit(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    await _require_chat(c, session_id)
    body = await _json_object(request)
    instruction = _as_text(body.get("instruction"), "instruction", 1000)
    if not instruction:
        raise _bad("instruction must not be empty.")
    item_id = str(body.get("item_id") or "")
    plan = await load_plan(c, session_id)
    message = instruction
    if item_id and plan is not None:
        item = plan.item_index().get(item_id)
        if item is not None:
            message = f"For the stop {item.name} (id {item.id}), {instruction}"
    from app.turn.orchestrator import Dependencies, collect_turn

    context = await load_context(c, session_id)
    await _note_operator_action(c, session_id, "edit", message)
    events = await collect_turn(
        Dependencies(settings=c.settings, store=c.store, operator=True),
        ChatRequest(session_id=session_id, message=message, locale=context.language, review=False),
        session_id=session_id,
        user_message=message,
    )
    reply = "".join(event.data.get("text", "") for event in events if event.event == "token")
    return {"reply": reply, "plan": _desk_plan(await load_plan(c, session_id))}


# -- review cases --------------------------------------------------------


@router.get("/api/admin/cases")
async def review_cases(request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    operator_cases = await c.store.get_kv("operator:cases") or []
    suggestions = await c.store.get_kv("suggestions") or []
    return {"cases": operator_cases, "suggestions": suggestions}


@router.post("/api/admin/cases")
async def add_case(request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    body = await request.json()
    cases = await c.store.get_kv("operator:cases") or []
    cases.append(
        {
            "source": "operator",
            "session_state": body.get("session_state", "live"),
            "run_id": "",
            "dataset": "operator",
            "case_id": f"op-{len(cases) + 1}",
            "kind": "added",
            "failure_class": "",
            "input": body.get("message", ""),
            "output_summary": "",
            "judge_reason": body.get("note", ""),
            "check_status": "chat",
            "check_summary": "",
            "expected": body.get("expected", ""),
            "session_id": body.get("session_id", ""),
        }
    )
    await c.store.set_kv("operator:cases", cases)
    return {"cases": cases, "suggestions": await c.store.get_kv("suggestions") or []}


@router.get("/api/admin/cases/turns")
async def capture_turns(request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    sessions = await c.store.list_sessions(40)
    turns = []
    for session in sessions:
        messages = await c.store.list_messages(session["id"], limit=40)
        for index, message in enumerate(messages):
            if message["role"] != "user":
                continue
            reply = next(
                (row["content"] for row in messages[index + 1 :] if row["role"] == "assistant"),
                "",
            )
            turns.append(
                {
                    "id": message["id"],
                    "session_id": session["id"],
                    "session_state": "live",
                    "input": message["content"],
                    "reply": reply[:400],
                }
            )
    return {"turns": turns[:80]}


@router.post("/api/admin/cases/scan")
async def scan_cases(request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    cases = await c.store.get_kv("operator:cases") or []
    suggestions = await c.store.get_kv("suggestions") or []
    if cases and not suggestions:
        suggestions.append(
            {
                "id": secrets.token_hex(6),
                "status": "draft",
                "skill": "understand",
                "summary": "Operator-added cases exist; review them against the turn analyst.",
                "rationale": f"{len(cases)} operator case(s) collected.",
                "body": "Prefer the traveller's intent wording when classifying ambiguous requests.",
                "case_ids": [row.get("case_id", "") for row in cases][:10],
                "created_at": now_iso(),
                "decided_at": None,
                "kind": "skill",
            }
        )
        await c.store.set_kv("suggestions", suggestions)
    return {"suggestions": suggestions}


@router.post("/api/admin/cases/check")
async def check_cases(request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    cases = await c.store.get_kv("operator:cases") or []
    passed = sum(1 for row in cases if row.get("check_status") == "pass")
    return {"passed": passed, "exam": len(cases), "cases": cases}


def _suggestion_action(request: Request, action: str):
    async def handler(suggestion_id: str, request: Request) -> dict[str, Any]:
        require_admin(request)
        c = container(request)
        suggestions = await c.store.get_kv("suggestions") or []
        row = next((item for item in suggestions if item.get("id") == suggestion_id), None)
        if row is None:
            raise HTTPException(status_code=404, detail="That suggestion is not there.")
        if action == "accept":
            body = await request.json()
            row["status"] = "accepted"
            row["body"] = str(body.get("body") or row.get("body") or "")
            overrides = await c.store.get_kv("prompts") or {}
            current = overrides.get(row.get("skill") or "understand")
            if current is not None and row["body"]:
                overrides[row["skill"]] = current + "\n\n" + row["body"]
                await c.store.set_kv("prompts", overrides)
        elif action == "dismiss":
            row["status"] = "dismissed"
        else:
            row["status"] = "restored"
        row["decided_at"] = now_iso()
        await c.store.set_kv("suggestions", suggestions)
        return {"suggestion": row, "ok": True}

    return handler


router.add_api_route("/api/admin/cases/suggestions/{suggestion_id}/accept", _suggestion_action(None, "accept"), methods=["POST"])
router.add_api_route("/api/admin/cases/suggestions/{suggestion_id}/dismiss", _suggestion_action(None, "dismiss"), methods=["POST"])
router.add_api_route("/api/admin/cases/suggestions/{suggestion_id}/restore", _suggestion_action(None, "restore"), methods=["POST"])


def _first_line(text: str) -> str:
    for line in text.splitlines():
        line = line.strip("# ").strip()
        if line:
            return line[:160]
    return ""
