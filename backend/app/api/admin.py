"""Operator surfaces: runtime, prompts, escalation, the desk, review cases and redaction."""

from __future__ import annotations

import json
import re
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import container, require_admin, require_session, user_public
from app.api.views import load_context, load_plan, runtime_config, save_plan, session_view
from app.core.dates import shift_time
from app.core.prompts import SKILL_DIR, skill_names
from app.domain.models import ChatRequest, TripPlan, now_iso
from app.plan.builder import refresh_derived

router = APIRouter()

DEFAULT_PATTERNS = {
    "hkid": {"label": "HKID", "pattern": r"[A-Z]{1,2}\d{6}\(\d\)"},
    "hk_phone": {"label": "HK phone", "pattern": r"\+852\s?\d{4}\s?\d{4}"},
    "email": {"label": "Email", "pattern": r"[\w.+-]+@[\w-]+\.[\w.]+"},
    "card": {"label": "Card number", "pattern": r"\b(?:\d[ -]*?){13,16}\b"},
}

ESCALATION_DEFAULT = {
    "enabled": True,
    "message": "A travel designer will join this chat.",
    "frustration_at": 0.75,
    "match_mode": "examples",
    "use_jev": False,
    "stuck_on": True,
    "stuck_after": 3,
    "words": ["useless", "terrible", "not working", "angry"],
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
    for key in ("thresholds", "freshness_hours", "writer", "reviewer", "redaction", "gate", "writer_model", "models", "data_mode"):
        if key in body and body[key] is not None:
            stored[key] = body[key]
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
    body = await request.json()
    stored = {
        key: body[key]
        for key in ("enabled", "message", "frustration_at", "match_mode", "use_jev", "stuck_on", "stuck_after", "words", "examples")
        if key in body
    }
    await c.store.set_kv("escalation", stored)
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
    events = [
        {"category": category, "severity": "high" if "reveal" in category else "medium", "count": int(count)}
        for category, count in counters.items()
    ]
    return {"events": events}


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


@router.get("/api/admin/desk")
async def desk_list(request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    sessions = await c.store.list_sessions(60)
    chats = []
    for session in sessions:
        messages = await c.store.list_messages(session["id"], limit=50)
        if not messages:
            continue
        context = await load_context(c, session["id"])
        last = messages[-1]
        chats.append(
            {
                **_desk_contact(context),
                "session_id": session["id"],
                "created_at": session["created_at"],
                "updated_at": session["updated_at"],
                "state": "live",
                "on_page": False,
                "finished": bool(context.finalized),
                "held": bool(session.get("held")),
                "agent_name": session.get("agent_name") or "",
                "stuck": False,
                "frustration": 0.0,
                "frustration_kind": "none",
                "band": "calm",
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
        "note": session.get("note") or "",
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
    user_id = request.headers.get("X-Admin-Token") or "operator"
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
    await c.store.set_held(session_id, False, "")
    return {"held": False}


@router.post("/api/admin/desk/{session_id}/note")
async def desk_note(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    body = await request.json()
    note = str(body.get("note", ""))[:500]
    await c.store.touch_session(session_id, planner_note=note)
    return {"note": note}


@router.post("/api/admin/desk/{session_id}/say")
async def desk_say(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    body = await request.json()
    text = str(body.get("text", ""))[:2000]
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
    body = await request.json()
    message = str(body.get("message", ""))[:2000]
    from app.turn.orchestrator import Dependencies, collect_turn

    context = await load_context(c, session_id)
    events = await collect_turn(
        Dependencies(settings=c.settings, store=c.store),
        ChatRequest(session_id=session_id, message=message, locale=context.language, review=False),
        session_id=session_id,
        user_message=message,
    )
    reply = "".join(event.data.get("text", "") for event in events if event.event == "token")
    intent = next((event.data.get("intent", "") for event in events if event.event == "done"), "")
    return {"reply": reply, "intent": intent}


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
    body = await request.json()
    plan = await load_plan(c, session_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="There is no plan.")
    context = await load_context(c, session_id)
    index = plan.item_index()
    for row in body.get("items") or []:
        item = index.get(row.get("id"))
        if item is None:
            continue
        if row.get("remove"):
            for day in plan.days:
                day.items = [entry for entry in day.items if entry.id != item.id]
            continue
        item.start = row.get("start") or item.start
        item.end = row.get("end") or item.end
        if row.get("name"):
            item.name = str(row["name"])[:160]
    refresh_derived(plan, context)
    plan = await save_plan(c, plan, "Edited at the desk.")
    return {"plan": _desk_plan(plan)}


@router.post("/api/admin/desk/{session_id}/brief")
async def desk_brief(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    body = await request.json()
    context = await load_context(c, session_id)
    for field in ("destination", "origin", "start_date", "end_date", "party_size", "budget", "currency", "preferences"):
        if body.get(field) not in (None, ""):
            setattr(context, field, body[field])
    if body.get("end_date") and body.get("start_date"):
        from app.core.dates import parse_date

        start, end = parse_date(body["start_date"]), parse_date(body["end_date"])
        if start and end:
            context.days = (end - start).days + 1
    await c.store.save_context(session_id, context.model_dump())
    return {"built": True, "plan": _desk_plan(await load_plan(c, session_id))}


@router.post("/api/admin/desk/{session_id}/layout")
async def desk_layout(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    body = await request.json()
    plan = await load_plan(c, session_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="There is no plan.")
    context = await load_context(c, session_id)
    index = plan.item_index()
    for row in body.get("items") or []:
        item = index.get(row.get("id"))
        if item is None:
            continue
        day_index = int(row.get("day") or 1)
        if not 1 <= day_index <= len(plan.days):
            continue
        for day in plan.days:
            day.items = [entry for entry in day.items if entry.id != item.id]
        item.start = row.get("start") or item.start
        item.end = row.get("end") or item.end
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
    body = await request.json()
    plan = await load_plan(c, session_id, int(body.get("version") or 0))
    if plan is None:
        raise HTTPException(status_code=404, detail="That version is not there.")
    plan = await save_plan(c, plan, f"Restored version {body.get('version')}.")
    return {"plan": plan.model_dump(mode="json")}


@router.post("/api/admin/desk/{session_id}/edit")
async def desk_edit(session_id: str, request: Request) -> dict[str, Any]:
    require_admin(request)
    c = container(request)
    body = await request.json()
    instruction = str(body.get("instruction", ""))[:1000]
    item_id = str(body.get("item_id", ""))
    plan = await load_plan(c, session_id)
    message = instruction
    if item_id and plan is not None:
        item = plan.item_index().get(item_id)
        if item is not None:
            message = f"For the stop {item.name} (id {item.id}), {instruction}"
    from app.turn.orchestrator import Dependencies, collect_turn

    context = await load_context(c, session_id)
    events = await collect_turn(
        Dependencies(settings=c.settings, store=c.store),
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
