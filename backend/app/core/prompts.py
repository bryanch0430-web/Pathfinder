"""Skill loading and the compact views prompts are built from.

A skill is a Markdown file under ``app/skills``.  An operator can override one at
runtime through ``/api/admin/prompts``; the file stays the default.  The prompts get
*views* of state, not raw objects, so the model sees a stable, small shape.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from app.domain.models import ChatMessage, TripContext, TripPlan

SKILL_DIR = Path(__file__).resolve().parent.parent / "skills"


@lru_cache(maxsize=32)
def _read(name: str) -> str:
    path = SKILL_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")


def skill(name: str, overrides: dict[str, str] | None = None, **substitutions: Any) -> str:
    body = (overrides or {}).get(name) or _read(name)
    for key, value in substitutions.items():
        body = body.replace("{{" + key + "}}", str(value))
    return body


def skill_names() -> list[str]:
    return sorted(path.stem for path in SKILL_DIR.glob("*.md"))


def context_brief(context: TripContext) -> dict[str, Any]:
    return {
        "destination": context.destination or None,
        "origin": context.origin or None,
        "start_date": context.start_date,
        "end_date": context.end_date,
        "days": context.days,
        "party_size": context.party_size,
        "budget": context.budget,
        "currency": context.currency,
        "hotel_style": context.hotel_style or None,
        "preferences": context.preferences[:12],
        "hard_constraints": context.hard_constraints[:12],
        "pace": context.pace,
        "profile": context.profile.model_dump() if context.profile else None,
        "stops": [stop.model_dump() for stop in context.stops] if context.stops else [],
        "one_way": context.one_way,
        "language": context.language,
        "finalized": context.finalized,
    }


def plan_outline(plan: TripPlan | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    return {
        "version": plan.version,
        "destination": plan.destination.name,
        "dates": {"start": plan.dates.start, "end": plan.dates.end},
        "days": [
            {
                "day": index,
                "date": day.date,
                "title": day.title,
                "items": [
                    {
                        "id": item.id,
                        "type": item.type,
                        "name": item.name,
                        "start": item.start,
                        "end": item.end,
                        "locked": item.locked,
                        "confirmed": item.confirmed,
                    }
                    for item in day.items
                ],
            }
            for index, day in enumerate(plan.days, start=1)
        ],
        "hotels": [
            {"id": hotel.id, "name": hotel.name, "locked": hotel.locked, "confirmed": hotel.confirmed}
            for hotel in plan.hotels
        ],
        "tickets": [{"id": ticket.id, "name": ticket.name, "selected": ticket.selected} for ticket in plan.tickets],
    }


def plan_text(plan: TripPlan | None, *, with_sources: bool = False) -> str:
    if plan is None:
        return "No plan yet."
    lines = [
        f"Trip to {plan.destination.name} ({plan.dates.start} to {plan.dates.end}), "
        f"{plan.party_size} traveller(s), budget "
        + (f"{plan.budget.amount:.0f} {plan.budget.currency}" if plan.budget else "not set")
        + f". {plan.summary}".strip()
    ]
    for index, day in enumerate(plan.days, start=1):
        weather = f" Weather: {day.weather.summary}." if day.weather and day.weather.summary else ""
        lines.append(f"Day {index} — {day.date} — {day.title}.{weather} {day.note}".strip())
        for item in day.items:
            price = f" ({item.price.amount:.0f} {item.price.currency})" if item.price else ""
            lines.append(f"  {item.start}-{item.end} {item.name} [{item.type}]{price}. {item.notes}".strip())
            if item.transfer is not None:
                lines.append(
                    f"    transfer: {item.transfer.minutes:.0f} min {item.transfer.mode} from the previous stop"
                )
            if with_sources and item.source_refs:
                lines.append("    source: " + "; ".join(ref.source or ref.title for ref in item.source_refs[:2]))
    if plan.hotels:
        hotel = plan.hotels[0]
        price = f" ({hotel.price_per_night.amount:.0f} {hotel.price_per_night.currency}/night)" if hotel.price_per_night else ""
        lines.append(f"Stay: {hotel.name}{price}. {hotel.notes}".strip())
    if plan.tickets:
        ticket = plan.tickets[0]
        lines.append(f"Travel: {ticket.name}. {ticket.notes}".strip())
    if plan.cost:
        lines.append(
            f"Cost: {plan.cost.total:.0f} {plan.cost.currency} ({plan.cost.status}"
            + (f", budget {plan.cost.budget:.0f}" if plan.cost.budget else "")
            + ")."
        )
        if plan.cost.lines:
            lines.append(
                "Cost lines: "
                + "; ".join(f"{line.label} {line.amount:.0f} {plan.cost.currency}" for line in plan.cost.lines)
                + ". Item prices are per person and are multiplied by the party size in the total."
            )
    if plan.data_quality.gaps:
        lines.append("Known gaps: " + "; ".join(gap.message for gap in plan.data_quality.gaps[:4]))
    return "\n".join(lines)


def dialogue(messages: Iterable[ChatMessage], limit: int = 8) -> list[dict[str, str]]:
    rows = []
    for message in list(messages)[-limit:]:
        content = (message.content or "").strip().replace("\n", " ")
        if len(content) > 400:
            content = content[:400] + "…"
        rows.append({"role": message.role, "text": content})
    return rows


def retrieved(results: list[dict[str, Any]], limit: int = 5) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in results[:limit]:
        out.append(
            {
                "title": str(row.get("title", ""))[:160],
                "url": str(row.get("url", ""))[:300],
                "snippet": str(row.get("snippet") or row.get("text") or "")[:600],
            }
        )
    return out
