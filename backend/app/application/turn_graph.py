"""The review of one plan or one edit: check, lengthen a short visit once, check again, then phrase.

This is an ordinary loop. One turn is one dict. The search, the clock rules and the
plan object stay where they are. The loop only decides whether the result is reasonable
and whether to try the visit table once more.

ponytail: one repair pass, and only for a visit the duration table says is too short.
A clock change uses a free gap. Adding or deleting a stop is a proposal, not a write.
Upgrade: a second search step when the reviewer's fix is "search".
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Sequence

from app.application.edit_reply import _schedule, speak_edit
from app.application.visit_length import minimum_minutes
from app.domain.models import Message

log = logging.getLogger(__name__)

_MAX_ATTEMPTS = 2


def _span(item: Any) -> tuple[int, int]:
    from app.application.modification import _clock_minutes

    return _clock_minutes(item.start), _clock_minutes(item.end)


def _short_stops(plan: Any) -> list[tuple[Any, Any, int, int]]:
    """Attractions whose clock is under the duration table. Locked stops stay."""
    found = []
    for day in getattr(plan, "days", []) or []:
        for item in getattr(day, "items", []) or []:
            if getattr(getattr(item, "type", None), "value", "") != "attraction":
                continue
            if getattr(item, "locked", False) or getattr(item, "confirmed", False):
                continue
            need = minimum_minutes(getattr(item, "name", ""), list(getattr(item, "source_types", []) or []))
            if not need:
                continue
            start, end = _span(item)
            if end - start < need:
                found.append((day, item, end - start, need))
    return found


def _lengthen(plan: Any) -> list[str]:
    """Move a short visit to a free stretch of the table's minimum. No overlapping clocks."""
    from app.application.modification import _fit_span, _hhmm

    notes: list[str] = []
    for day, item, _current, need in _short_stops(plan):
        start, _end = _span(item)
        slot = _fit_span(day, need, start, item.id)
        if slot is None:
            notes.append(
                f"{item.name} stays {item.start}–{item.end}; a {need} minute visit does not fit this day."
            )
            continue
        item.start, item.end = _hhmm(slot[0]), _hhmm(slot[1])
        notes.append(f"{item.name} now runs {item.start}–{item.end} ({need} min).")
    return notes


def _permission_proposals(plan: Any) -> list[dict]:
    """One pending removal per day when a free gap cannot hold the minimum.

    The live plan is not changed. Accepting the proposal writes the copy where that
    one stop is gone and the short visit has been lengthened.
    ponytail: tries one stop at a time, attractions before meals, and stops at the
    first removal that fits. A day that needs two deletions gets no proposal.
    """
    proposals: list[dict] = []
    offered_days: set[int] = set()
    for day, item, _current, need in _short_stops(plan):
        number = list(plan.days).index(day) + 1
        if number in offered_days:
            continue
        for other in _removal_candidates(day, item):
            trial = plan.model_copy(deep=True)
            if not _drop_and_lengthen(trial, item.id, other.id, need):
                continue
            kept = _item_by_id(trial, item.id)
            if kept is None:
                continue
            proposals.append(
                {
                    "id": f"make-room-{item.id}",
                    "label": (
                        f"Remove {other.name} from Day {number} so {item.name} "
                        f"can run {need} minutes ({kept.start}–{kept.end})"
                    ),
                    "detail": (
                        "This removes a stop that is already on the plan. "
                        "Nothing is added or deleted until you accept."
                    ),
                    "status": "pending",
                    "plan": trial.model_dump(mode="json"),
                }
            )
            offered_days.add(number)
            break
        if len(proposals) >= 2:
            break
    return proposals


def _removal_candidates(day: Any, item: Any) -> list[Any]:
    rest = []
    for other in getattr(day, "items", []) or []:
        if other.id == item.id or getattr(other, "locked", False) or getattr(other, "confirmed", False):
            continue
        rest.append(other)
    attractions = [other for other in rest if getattr(getattr(other, "type", None), "value", "") == "attraction"]
    meals = [other for other in rest if getattr(getattr(other, "type", None), "value", "") == "meal"]
    return attractions + meals


def _drop_and_lengthen(plan: Any, keep_id: str, drop_id: str, need: int) -> bool:
    for day in plan.days:
        kept = [row for row in day.items if row.id != drop_id]
        if len(kept) != len(day.items):
            day.items = kept
    _lengthen(plan)
    kept_item = _item_by_id(plan, keep_id)
    if kept_item is None:
        return False
    start, end = _span(kept_item)
    return end - start >= need


def _item_by_id(plan: Any, item_id: str) -> Any:
    for day in getattr(plan, "days", []) or []:
        for item in getattr(day, "items", []) or []:
            if item.id == item_id:
                return item
    return None


def _skill_body(name: str) -> str:
    from app.application.skills import skill_by_name

    skill = skill_by_name(name)
    return skill.body.strip() if skill is not None and skill.body.strip() else ""


def _review_system() -> str:
    """The reasonableness skill, then the edit-review contract. Both are read on every pass."""
    parts = [body for body in (_skill_body("check-reasonable"), _skill_body("turn-review")) if body]
    if parts:
        return "\n\n".join(parts)
    return 'Return JSON only: {"verdict":"pass","fix":"none","issue":""}'


async def _duration_pages(search: Any, plan: Any, message: str) -> str:
    """Up to two web searches for how long a named stop usually takes. Empty when search is off."""
    if search is None:
        return ""
    names: list[str] = []
    for _day, item, _current, _need in _short_stops(plan):
        names.append(item.name)
    text = (message or "").casefold()
    for day in getattr(plan, "days", []) or []:
        for item in getattr(day, "items", []) or []:
            folded = item.name.casefold()
            if folded and folded in text and item.name not in names:
                names.append(item.name)
    lines: list[str] = []
    for name in names[:2]:
        try:
            rows = await search(f"{name} recommended visit duration")
        except Exception:
            log.warning("duration search failed for %s", name, exc_info=True)
            continue
        for row in list(rows or [])[:2]:
            title = str(getattr(row, "title", "") or "")
            snippet = str(getattr(row, "snippet", "") or getattr(row, "summary", "") or "")
            if title or snippet:
                lines.append(f"- {title}: {snippet[:240]}")
    if not lines:
        return ""
    return "Duration pages (suggestions from web search, not new stops):\n" + "\n".join(lines)


async def _model_verdict(settings: Any, gateway: Any, state: dict[str, Any], search: Any) -> tuple[str, str, str, str, dict[str, Any] | None]:
    """Ask the reviewer, or the writer when no reviewer is configured.

    Returns verdict, fix, issue, the trace label, and the call to log. A failure
    is a pass: the duration table still forces a retry on its own.
    """
    pages = await _duration_pages(search, state.get("plan"), state.get("message") or "")
    criteria = _skill_body("check-reasonable")
    user = (
        (f"Reasonableness skill (apply these criteria):\n{criteria}\n\n" if criteria else "")
        + f"Traveller:\n{state.get('message') or ''}\n\n"
        f"What the edit did:\n{state.get('facts') or ''}\n\n"
        f"Plan now:\n{_schedule(state.get('plan'))}"
        + (f"\n\n{pages}" if pages else "")
    )
    raw: dict[str, Any] | None = None
    model_name = str(getattr(settings, "reviewer_model", "") or "").strip()
    label = f"reviewer {model_name}" if model_name else "writer"
    logged: dict[str, Any] | None = None
    started = time.perf_counter()
    try:
        from app.application.plan_review import reviewer_llm

        reviewer = reviewer_llm(settings)
        if reviewer is not None and getattr(reviewer, "available", False):
            from app.application.llm_gateway import _extract_json

            model_name = str(getattr(reviewer, "name", "") or model_name or "reviewer")
            label = f"reviewer {model_name}"
            budget = float(getattr(settings, "reviewer_timeout_seconds", 20.0) or 20.0)
            started = time.perf_counter()
            try:
                # retries=0: one budget. The default of two retries turned a dead call into a blank 77s row.
                result = await reviewer.complete(
                    system=_review_system(),
                    user=user,
                    purpose="turn.review",
                    json_mode=True,
                    temperature=0,
                    max_tokens=600,
                    timeout=budget,
                    retries=0,
                )
            except Exception as exc:
                return "pass", "none", "", label, _failed_review(model_name, user, started, exc)
            raw = _extract_json(result.text)
            label = f"reviewer {result.model or model_name}".strip()
            logged = {
                "model": str(result.model or model_name),
                "user": user,
                "text": str(result.text or ""),
                "reasoning": str(getattr(result, "reasoning", "") or ""),
                "latency_ms": int(getattr(result, "latency_ms", 0) or 0),
                "prompt_tokens": int(getattr(result, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(result, "completion_tokens", 0) or 0),
            }
        elif gateway is not None and getattr(gateway, "available", False):
            raw = await gateway.json("turn-review", user=user, purpose="turn.review")
            writer = state.get("writer_model") or getattr(getattr(gateway, "llm", None), "name", "") or "writer"
            label = f"writer {writer} (no reviewer configured)"
    except Exception as exc:
        log.warning("turn review had no model verdict", exc_info=True)
        return "pass", "none", "", label, _failed_review(model_name or "writer", user, started, exc)
    if not isinstance(raw, dict):
        return "pass", "none", "", label, logged
    verdict = "retry" if str(raw.get("verdict") or "").strip().lower() == "retry" else "pass"
    fix = "lengthen" if str(raw.get("fix") or "").strip().lower() == "lengthen" else "none"
    issue = str(raw.get("issue") or "").strip()[:400]
    return verdict, fix, issue, label, logged


def _failed_review(model: str, user: str, started: float, exc: BaseException) -> dict[str, Any]:
    """The call that produced no verdict, so the decision log is not an empty row."""
    return {
        "model": model or "writer",
        "user": user,
        "text": f"No answer ({exc.__class__.__name__}). Treated as a pass.",
        "reasoning": "",
        "latency_ms": max(0, int((time.perf_counter() - started) * 1000)),
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }


def _log_review(gateway: Any, logged: dict[str, Any] | None, *, note: str) -> None:
    """Write the review call into the same decision log as the edit and the reply.

    The reviewer is not the writer gateway, so nothing records it unless this does.
    ``note`` is the visit table's override, when it replaced the model's verdict.
    """
    if not logged:
        return
    reasoning = logged["reasoning"].strip()
    if note:
        reasoning = f"{reasoning}\n{note}".strip()
    output = logged["text"].strip()
    tracer = getattr(gateway, "tracer", None)
    record = getattr(tracer, "record_generation", None)
    if callable(record):
        try:
            record(
                purpose="turn.review",
                model=logged["model"],
                prompt_tokens=logged["prompt_tokens"],
                completion_tokens=logged["completion_tokens"],
                latency_ms=logged["latency_ms"],
                reasoning=reasoning[:4000],
                output_preview=output[:4000],
                user_preview=logged["user"][:8000],
            )
        except Exception:
            log.warning("turn review was not logged", exc_info=True)
    try:
        from app.infrastructure.observability import developer_view, emit_live

        emit_live(
            "think",
            {
                "purpose": "turn.review",
                "model": logged["model"],
                "latency_ms": logged["latency_ms"],
                "reasoning": reasoning[:4000] if developer_view() else "",
                "output_preview": output[:4000] if developer_view() else "",
            },
        )
    except Exception:
        log.warning("turn review was not shown live", exc_info=True)


def _emit_step(on_step: Callable[[dict[str, Any]], None] | None, **data: Any) -> None:
    if on_step is None:
        return
    try:
        on_step(data)
    except Exception:
        log.warning("turn step was not delivered", exc_info=True)


def _model_of(label: str) -> str:
    text = (label or "").strip()
    if text.startswith("reviewer "):
        return text[len("reviewer ") :].strip() or text
    if text.startswith("writer "):
        return text[len("writer ") :].split(" (", 1)[0].strip() or text
    return text


def _needs_repair(state: dict[str, Any]) -> bool:
    return (
        state.get("verdict") == "retry"
        and state.get("fix") == "lengthen"
        and int(state.get("attempt") or 0) < _MAX_ATTEMPTS
    )


async def _review_loop(
    settings: Any,
    gateway: Any,
    state: dict[str, Any],
    search: Any = None,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Check, repair at most once, check again, then phrase. Same order as the old graph."""

    async def check() -> None:
        attempt = int(state.get("attempt") or 0) + 1
        started = time.perf_counter()
        preview = str(getattr(settings, "reviewer_model", "") or "reviewer")
        _emit_step(on_step, id=f"review-{attempt}", phase="review", model=preview, status="running")
        verdict, fix, issue, label, logged = await _model_verdict(settings, gateway, state, search)
        _emit_step(
            on_step,
            id=f"review-{attempt}",
            phase="review",
            model=_model_of(label) or preview,
            status="done",
            seconds=round(time.perf_counter() - started, 1),
        )
        shorts = _short_stops(state.get("plan"))
        trace = list(state.get("trace") or [])
        if shorts:
            # The table is the evidence. A model pass does not keep a short theme park.
            bits = [f"{item.name} is {current} min; this kind of place needs {need}" for _day, item, current, need in shorts]
            verdict, fix = "retry", "lengthen"
            issue = "; ".join(bits)
            trace.append("visit table: " + issue)
        _log_review(gateway, logged, note=("Visit table: " + issue) if shorts else "")
        trace.append(f"{label}: review → {verdict}" + (f" ({issue})" if issue and not shorts else ""))
        state.update({"verdict": verdict, "fix": fix, "issue": issue, "attempt": attempt, "trace": trace})

    def repair() -> None:
        started = time.perf_counter()
        notes = _lengthen(state.get("plan"))
        _emit_step(
            on_step,
            id=f"clock-{state.get('attempt') or 1}",
            phase="clock",
            model="application",
            status="done",
            seconds=round(time.perf_counter() - started, 1),
        )
        trace = list(state.get("trace") or [])
        trace.append("application: " + ("; ".join(notes) if notes else "no clock change"))
        pending = _permission_proposals(state.get("plan"))
        facts = (state.get("facts") or "").strip()
        if notes:
            facts = (facts + " " + " ".join(notes)).strip()
        if pending:
            asked = " ".join(item["label"] for item in pending)
            facts = (facts + " " + asked + " Nothing is added or removed until you accept.").strip()
        state.update({"facts": facts, "trace": trace, "permissions": pending})

    async def phrase() -> None:
        facts = state.get("facts") or ""
        if state.get("phrase_reply") is False:
            state["reply"] = facts.rstrip()
            return
        if state.get("verdict") == "retry" and state.get("issue") and state.get("fix") != "lengthen":
            facts = f"{facts} Review: {state['issue']}".strip()
        writer = state.get("writer_model") or "writer"
        started = time.perf_counter()
        _emit_step(on_step, id="reply", phase="reply", model=writer, status="running")
        try:
            spoken = await speak_edit(
                gateway,
                message=state.get("message") or "",
                history=state.get("history") or (),
                plan=state.get("plan"),
                facts=facts,
            )
            text = spoken or facts
        finally:
            _emit_step(
                on_step,
                id="reply",
                phase="reply",
                model=writer,
                status="done",
                seconds=round(time.perf_counter() - started, 1),
            )
        state["reply"] = text.rstrip()

    await check()
    if _needs_repair(state):
        repair()
        await check()
    await phrase()
    return state


async def review_and_reply(
    settings: Any,
    gateway: Any,
    *,
    message: str,
    history: Sequence[Message],
    plan: Any,
    facts: str,
    route_model: str = "",
    search: Any = None,
    on_step: Callable[[dict[str, Any]], None] | None = None,
    phrase: bool = True,
    permissions: list | None = None,
) -> str:
    """Check, maybe lengthen once, check again, then phrase. The factual sentence is kept if that fails.

    ``permissions`` collects a removal the traveller must accept. The plan is not changed
    for that removal. ``phrase`` is false for a first draft, which keeps its own summary.
    """
    writer = ""
    try:
        writer = str(getattr(getattr(gateway, "llm", None), "name", "") or "")
    except Exception:
        writer = ""
    try:
        result = await _review_loop(
            settings,
            gateway,
            {
                "message": message,
                "history": list(history or ()),
                "plan": plan,
                "facts": facts,
                "route_model": route_model,
                "writer_model": writer,
                "attempt": 0,
                "trace": [],
                "phrase_reply": phrase,
            },
            search,
            on_step,
        )
    except Exception:
        log.warning("review fell back to the factual sentence", exc_info=True)
        return facts
    if permissions is not None:
        permissions.extend(list(result.get("permissions") or []))
    reply = str(result.get("reply") or "").strip()
    return reply or facts
