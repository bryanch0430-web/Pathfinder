"""The sentence the traveller reads after an edit.

Python still applies the change and records the facts. This call only phrases
them, from the plan and the earlier turns. A missing or unusable answer leaves
the factual sentence in place.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Sequence

from app.application.prompt_context import conversation_block, with_conversation
from app.domain.models import Message
from app.security.guard import fence_user_message

log = logging.getLogger(__name__)

#: Longer than the extract prompt on purpose: that call only returns a small JSON object.
_REPLY_TURNS = 16
_REPLY_CHARS = 800
_REPLY_CAP = _REPLY_TURNS * _REPLY_CHARS


def _schedule(plan: Any) -> str:
    """The days as they stand now: name and clock only, so the reply cannot invent a pin."""
    days = []
    for number, day in enumerate(getattr(plan, "days", []) or [], start=1):
        stops = []
        for item in getattr(day, "items", []) or []:
            kind = getattr(getattr(item, "type", ""), "value", "") or str(getattr(item, "type", ""))
            stops.append(
                {"name": item.name, "start": item.start, "end": item.end, "kind": kind}
            )
        days.append({"day": number, "date": str(getattr(day, "date", "")), "stops": stops})
    return json.dumps({"days": days}, ensure_ascii=False)


def usable_reply(text: str) -> str:
    """A spoken reply, or "" when the model returned JSON, a fence, or nothing."""
    cleaned = (text or "").strip()
    if not cleaned or cleaned[0] in "{[":
        return ""
    if cleaned.startswith("```"):
        return ""
    if len(cleaned) > 1800:
        cleaned = cleaned[:1800].rsplit(" ", 1)[0].rstrip()
    return cleaned


async def speak_edit(
    gateway: Any,
    *,
    message: str,
    history: Sequence[Message],
    plan: Any,
    facts: str,
) -> str:
    """The model's wording of ``facts``, or "" so the caller keeps the factual sentence."""
    if not getattr(gateway, "available", False) or not (facts or "").strip():
        return ""
    user = (
        "What the edit already did (these are the facts; do not add a place or a time that is not here):\n"
        f"{facts.strip()}\n\n"
        f"Plan now:\n{_schedule(plan)}\n\n"
        + with_conversation(
            conversation_block(
                history,
                message,
                limit=_REPLY_TURNS,
                chars=_REPLY_CHARS,
                cap=_REPLY_CAP,
            )
        )
        + fence_user_message(message)
    )
    try:
        # Warmer and longer than the JSON extract, and a short reasoning pass on this call only.
        text = await gateway.text(
            "reply",
            user=user,
            purpose="reply.edit",
            temperature=0.7,
            max_tokens=4096,
            reasoning="low",
        )
    except Exception:
        log.warning("edit reply fell back to the factual sentence", exc_info=True)
        return ""
    return usable_reply(text)
