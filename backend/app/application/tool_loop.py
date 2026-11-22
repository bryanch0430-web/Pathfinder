"""One look, then the result goes back to the model. At most three looks.

The model names a tool or stops. A tool result is an observation on the next call.
A model that asks for nothing, or a failed call, leaves the path to act on what it already has.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from app.domain.errors import CanaryLeak

log = logging.getLogger(__name__)

#: A path may look this many times. The next step is the path's own action.
MAX_LOOKS = 3
_TOOLS = frozenset({"web.search", "web.fetch", "places.search", "weather.forecast"})


async def gather(
    gateway: Any,
    toolbox: Any,
    registry: Any,
    *,
    message: str,
    context: Any,
    plan: Any,
    on_step: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    """Notes from the looks the model asked for. Empty when it asks for none."""
    if gateway is None or not getattr(gateway, "available", False) or registry is None:
        return ""
    from app.security import fence_user_message

    notes: list[str] = []
    for index in range(1, MAX_LOOKS + 1):
        looked = "\n".join(notes)
        user = (
            "Trip destination: "
            + str(getattr(context, "destination", "") or "unknown")
            + "\n"
            + (f"Already looked up:\n{looked}\n\n" if looked else "")
            + fence_user_message(message)
        )
        try:
            data = await gateway.json("tool-round", user=user, purpose="tool.round")
        except CanaryLeak:
            raise
        except Exception:
            log.warning("a look-up decision failed open", exc_info=True)
            break
        if not isinstance(data, dict):
            break
        tool = str(data.get("tool") or "").strip()
        if tool not in _TOOLS or tool not in getattr(toolbox, "allowed", ()):
            break
        if on_step is not None:
            on_step({"id": f"lookup-{index}", "phase": "lookup", "model": tool, "status": "running"})
        note = await _one(tool, data, toolbox, registry, context, plan)
        line = f"{tool}: {note}"
        if line in notes:
            if on_step is not None:
                on_step(
                    {
                        "id": f"lookup-{index}",
                        "phase": "lookup",
                        "model": tool,
                        "status": "done",
                        "note": note[:240],
                    }
                )
            break
        notes.append(line)
        if on_step is not None:
            on_step(
                {
                    "id": f"lookup-{index}",
                    "phase": "lookup",
                    "model": tool,
                    "status": "done",
                    "note": note[:240],
                }
            )
    return "\n".join(notes)


async def _one(tool: str, data: dict, toolbox: Any, registry: Any, context: Any, plan: Any) -> str:
    try:
        if tool == "web.search":
            return await _search(data, toolbox, registry)
        if tool == "web.fetch":
            return await _fetch(data, toolbox, registry)
        if tool == "places.search":
            return await _places(data, toolbox, registry, context, plan)
        if tool == "weather.forecast":
            return await _weather(toolbox, registry, context, plan)
    except Exception as exc:
        return _clip(str(exc) or "the look-up failed")
    return "not used"


async def _search(data: dict, toolbox: Any, registry: Any) -> str:
    source = next((item for item in (getattr(registry, "web", None) or []) if getattr(item, "available", False)), None)
    query = str(data.get("query") or "").strip()
    if source is None or not query:
        return "no web search is configured" if source is None else "no query"
    rows = await toolbox.call("web.search", source.search, query=query[:128], count=4)
    lines = []
    for row in list(rows or [])[:4]:
        title = str(getattr(row, "title", "") or "")
        snippet = str(getattr(row, "snippet", "") or getattr(row, "content", "") or "")
        lines.append(" ".join(part for part in (title, snippet) if part))
    return _clip("; ".join(line for line in lines if line) or "no pages")


async def _fetch(data: dict, toolbox: Any, registry: Any) -> str:
    documents = getattr(registry, "documents", None)
    url = str(data.get("url") or "").strip()
    if documents is None or not url:
        return "no page reader" if documents is None else "no url"
    fetched = await toolbox.call("web.fetch", documents.fetch, url=url)
    title = str(getattr(fetched, "title", "") or "")
    text = str(getattr(fetched, "text", "") or "")
    if getattr(fetched, "data", "") and not text:
        return _clip(f"a picture ({title or url})")
    return _clip(f"{title} {text}".strip() or "the page had no text")


async def _places(data: dict, toolbox: Any, registry: Any, context: Any, plan: Any) -> str:
    source = next((item for item in (getattr(registry, "places", None) or []) if getattr(item, "available", True)), None)
    query = str(data.get("query") or "").strip()
    if source is None or not query:
        return "no place search" if source is None else "no query"
    destination = ""
    geo = None
    if plan is not None and getattr(plan, "destination", None) is not None:
        destination = str(plan.destination.name or "")
        geo = plan.destination.geo
    if not destination:
        destination = str(getattr(context, "destination", "") or "")
    rows = await toolbox.call(
        "places.search",
        source.search,
        destination=destination or query,
        geo=geo,
        category="attraction",
        limit=5,
        query=query[:80],
    )
    names = [str(getattr(row, "name", "") or "") for row in list(rows or [])[:5]]
    return _clip(", ".join(name for name in names if name) or "no places")


async def _weather(toolbox: Any, registry: Any, context: Any, plan: Any) -> str:
    sources = getattr(registry, "weather", None) or []
    source = sources[0] if sources else None
    start = getattr(getattr(plan, "dates", None), "start", None) or getattr(context, "start_date", None)
    end = getattr(getattr(plan, "dates", None), "end", None) or getattr(context, "end_date", None)
    geo = getattr(getattr(plan, "destination", None), "geo", None)
    if source is None or start is None or end is None or geo is None:
        return "the forecast needs a destination and dates"
    rows = await toolbox.call("weather.forecast", source.forecast, geo=geo, start=start, end=end)
    lines = []
    for row in list(rows or [])[:4]:
        dumped = row.model_dump(mode="json") if hasattr(row, "model_dump") else {}
        lines.append(" ".join(str(dumped.get(key) or "") for key in ("date", "summary", "condition") if dumped.get(key)))
    return _clip("; ".join(line for line in lines if line) or "no forecast")


def _clip(text: str, limit: int = 700) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
