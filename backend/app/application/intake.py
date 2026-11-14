"""Intake: a trip started from what the traveller shares (a link, a PDF or a picture).

The shared material is read (a link through the logged ``web.fetch`` tool, a PDF by its text layer,
a picture by a writer that can see), and one writer call extracts what it says about a trip: the
place, dates, length, budget, the places it names and the wishes it states. What is extracted fills
only the empty fields of the trip form; the named places become must-see wishes. The trip is then
understood, routed, planned, refined and assessed like any other, so every stop of the plan still
comes from the planner's own sources and never from the shared text itself.

The shared material is untrusted. Its text is screened for instructions before any prompt sees it,
the writer's reply is checked field by field, and a place or destination is kept only when the
material really names it (pictures cannot be checked that way and are taken on the writer's word).
The step fails open: a source that cannot be read is reported and the turn goes on without it. A
canary leak is the one failure that is raised.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Sequence

from pydantic import BaseModel

from app.application.llm_gateway import LLMGateway
from app.application.toolbox import Toolbox
from app.config import Settings
from app.domain.errors import CanaryLeak, ProviderError
from app.domain.models import (
    TRIP_LINE_MAX_LENGTH,
    TRIP_LINES_MAX,
    TRIP_STOPS_MAX,
    TripContext,
    TripStop,
)
from app.domain.ports import ImageInput
from app.security import strip_fence_tags, strip_fence_tags_deep
from app.security.untrusted import neutralise

_URL = re.compile(r"(?i)\bhttps?://[^\s<>\"'　-〿＀-￯]+")
_URL_ANYWHERE = re.compile(r"(?i)https?://|www\.")
_TRAILING = ".,;:!?)]}>\"'"
#: What a link in the message is replaced with before the message is understood and routed.
LINK_WORDS = "the link I shared"
_PLACE_MIN, _PLACE_MAX, _WISH_MAX, _WISHES_MAX = 2, 60, 120, 5
UNSUPPORTED = "Only PDFs and PNG, JPEG, WebP or GIF pictures can be read."
NO_SIGHT = "The current model could not read the picture."


@dataclass(frozen=True)
class Attachment:
    """A file the traveller attached to the message."""

    name: str
    media_type: str
    data: bytes


@dataclass
class IntakeSource:
    name: str
    kind: str  # "url", "pdf", "image" or "text"
    status: str = "read"  # "read" or "failed"
    note: str = ""


@dataclass
class IntakeResult:
    sources: list[IntakeSource] = field(default_factory=list)
    #: Trip form fields the shared material filled.
    filled: list[str] = field(default_factory=list)
    places: list[str] = field(default_factory=list)
    wishes: list[str] = field(default_factory=list)

    @property
    def read(self) -> bool:
        return any(source.status == "read" for source in self.sources)

    def event(self) -> dict[str, Any]:
        return {
            "sources": [
                {"name": s.name, "kind": s.kind, "status": s.status, "note": s.note}
                for s in self.sources
            ],
            "filled": self.filled,
            "places": self.places,
        }


class _IntakeDraft(BaseModel):
    """The writer's raw reply, lenient on purpose: each field is checked on its own in ``_apply``."""

    destination: Any = None
    origin: Any = None
    start_date: Any = None
    end_date: Any = None
    days: Any = None
    budget: Any = None
    currency: Any = None
    places: Any = None
    wishes: Any = None
    stops: Any = None


def find_urls(message: str, limit: int) -> list[str]:
    """The links in the message, in order, without the punctuation that follows them."""
    urls: list[str] = []
    for match in _URL.finditer(message or ""):
        url = match.group(0).rstrip(_TRAILING)
        if url not in urls:
            urls.append(url)
    return urls[: max(limit, 0)]


def without_links(message: str) -> str:
    """The message as it is understood and routed: a link is data to read, not words about the trip."""
    return _URL.sub(LINK_WORDS, message or "").strip()


def _key(text: Any) -> str:
    return " ".join(str(text or "").split()).casefold()


def _screened(text: str) -> str:
    """Fetched or uploaded text with instruction-like content replaced, as every tool result is."""
    return str(neutralise(text, []))


def _line(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return "" if not text or _URL_ANYWHERE.search(text) else text[:limit]


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


async def _gather(
    message: str,
    attachments: Sequence[Attachment],
    *,
    reader: Any,
    toolbox: Toolbox,
    settings: Settings,
) -> tuple[
    list[IntakeSource], list[dict[str, str]], list[ImageInput], list[IntakeSource]
]:
    """Read every source. Returns the sources, the text blocks, the pictures and the pictures' own sources."""
    sources: list[IntakeSource] = []
    texts: list[dict[str, str]] = []
    images: list[ImageInput] = []
    image_sources: list[IntakeSource] = []

    def add_image(source: IntakeSource, media_type: str, data_b64: str) -> None:
        if len(images) >= settings.intake_max_attachments:
            source.status, source.note = "failed", "Too many pictures in one message."
            return
        images.append(ImageInput(media_type=media_type, data=data_b64))
        image_sources.append(source)

    async def open_link(url: str) -> Any:
        return await toolbox.call("web.fetch", reader.fetch, url=url)

    urls = find_urls(message, settings.intake_max_urls) if reader is not None else []
    fetched = await asyncio.gather(
        *(open_link(url) for url in urls), return_exceptions=True
    )
    for url, outcome in zip(urls, fetched):
        source = IntakeSource(name=url[:200], kind="url")
        sources.append(source)
        if isinstance(outcome, CanaryLeak):
            raise outcome
        if isinstance(outcome, ProviderError):
            source.status, source.note = "failed", str(outcome)[:200]
        elif isinstance(outcome, BaseException):
            source.status, source.note = "failed", "The link could not be read."
        elif outcome.kind == "image":
            source.kind = "image"
            add_image(source, outcome.media_type, outcome.data)
        else:
            texts.append(
                {
                    "name": source.name,
                    "kind": outcome.kind,
                    "title": outcome.title,
                    "text": outcome.text,
                }
            )

    import base64

    for attachment in list(attachments)[: settings.intake_max_attachments]:
        source = IntakeSource(name=(attachment.name or "attachment")[:200], kind="text")
        sources.append(source)
        kind, media_type = (
            reader.describe(attachment.data) if reader is not None else ("", "")
        )
        if kind == "pdf":
            source.kind = "pdf"
            try:
                text = _screened(await reader.read_pdf(data=attachment.data))
            except ProviderError as exc:
                source.status, source.note = "failed", str(exc)[:200]
                continue
            except Exception:
                source.status, source.note = "failed", "The PDF could not be read."
                continue
            if not text.strip():
                source.status, source.note = (
                    "failed",
                    "The PDF has no text to read (it may be a scan).",
                )
                continue
            texts.append(
                {"name": source.name, "kind": "pdf", "title": "", "text": text}
            )
        elif kind == "image":
            source.kind = "image"
            add_image(source, media_type, base64.b64encode(attachment.data).decode())
        else:
            source.status, source.note = "failed", UNSUPPORTED
    return sources, texts, images, image_sources


def _apply(
    draft: _IntakeDraft,
    context: TripContext,
    *,
    haystack: str,
    sighted: bool,
    today: date,
    settings: Settings,
) -> IntakeResult:
    """Fill the form's empty fields from the checked reply. ``sighted``: pictures were read, so a name the
    texts do not hold may come from a picture and is kept."""
    result = IntakeResult()

    def named(text: str) -> bool:
        return bool(text) and (sighted or _key(text) in haystack)

    destination = _line(draft.destination, 80)
    if named(destination) and not context.destination.strip():
        context.destination = destination
        result.filled.append("destination")
    stops = [
        (_line(row.get("city"), 80), row.get("nights"))
        for row in (draft.stops if isinstance(draft.stops, list) else [])
        if isinstance(row, dict)
    ]
    stops = [(city, nights) for city, nights in stops if named(city)]
    if 2 <= len(stops) <= TRIP_STOPS_MAX and not context.stops:
        # The cities win over whatever the form held as a destination (a country or region, or a later city).
        replaced = _key(context.destination) not in ("", _key(stops[0][0]))
        context.stops = [
            TripStop(
                city=city,
                nights=nights if isinstance(nights, int) and not isinstance(nights, bool) and 1 <= nights <= 60 else None,
            )
            for city, nights in stops
        ]
        context.destination = stops[0][0]
        result.filled.append("destination")
        if replaced:
            result.filled.append("stops")
    origin = _line(draft.origin, 80)
    if named(origin) and not context.origin.strip():
        context.origin = origin
        result.filled.append("origin")

    start, end = _day(draft.start_date), _day(draft.end_date)
    try:
        days = int(draft.days) if draft.days is not None else None
    except (TypeError, ValueError):
        days = None
    if days is not None and not 1 <= days <= settings.max_days:
        days = None
    if context.start_date is None and start is not None and start >= today:
        context.start_date = start
        if end is not None and start <= end and (end - start).days < settings.max_days:
            context.end_date = end
        result.filled.append("dates")
    if context.end_date is None and context.days is None and days is not None:
        context.days = days
        result.filled.append("days")

    try:
        budget = float(draft.budget) if draft.budget is not None else None
    except (TypeError, ValueError):
        budget = None
    if budget is not None and budget > 0 and context.budget is None:
        context.budget = budget
        currency = str(draft.currency or "").strip().upper()
        if re.fullmatch(r"[A-Z]{3}", currency):
            context.currency = currency
        result.filled.append("budget")

    room = TRIP_LINES_MAX - len(context.preferences)
    held = {_key(line) for line in context.preferences}
    for raw in draft.places if isinstance(draft.places, list) else []:
        place = _line(raw, _PLACE_MAX)
        if (
            len(place) < _PLACE_MIN
            or not named(place)
            or len(result.places) >= settings.intake_max_places
        ):
            continue
        line = f"must-see: {place}"[:TRIP_LINE_MAX_LENGTH]
        if _key(line) in held or room <= 0:
            continue
        context.preferences.append(line)
        held.add(_key(line))
        result.places.append(place)
        room -= 1
    for raw in draft.wishes if isinstance(draft.wishes, list) else []:
        wish = _screened(_line(raw, _WISH_MAX))
        if (
            not wish
            or _key(wish) in held
            or room <= 0
            or len(result.wishes) >= _WISHES_MAX
        ):
            continue
        if wish.lower().startswith("must-see:"):
            continue  # a place comes through `places`, where it is checked against the material
        context.preferences.append(wish)
        held.add(_key(wish))
        result.wishes.append(wish)
        room -= 1
    if result.places or result.wishes:
        result.filled.append("preferences")
    return result


async def run_intake(
    *,
    message: str,
    attachments: Sequence[Attachment],
    context: TripContext,
    gateway: LLMGateway,
    reader: Any,
    toolbox: Toolbox,
    settings: Settings,
    today: date | None = None,
) -> IntakeResult:
    """Read what the message shares and fill ``context``'s empty fields from it (in place)."""
    today = today or date.today()
    sources, texts, images, image_sources = await _gather(
        message, attachments, reader=reader, toolbox=toolbox, settings=settings
    )
    result = IntakeResult(sources=sources)
    if not texts and not images:
        return result
    if not gateway.available:
        for source in sources:
            if source.status == "read":
                source.status, source.note = (
                    "failed",
                    "No model is configured to read it.",
                )
        return result

    share = max(settings.intake_max_chars // max(len(texts), 1), 500)
    payload = {
        "today": today.isoformat(),
        "message": strip_fence_tags(without_links(message))[:2000],
        "sources": [{**block, "text": block["text"][:share]} for block in texts],
        "pictures": len(images),
    }
    user = json.dumps(strip_fence_tags_deep(payload), ensure_ascii=False, default=str)

    async def ask(pictures: list[ImageInput]) -> _IntakeDraft:
        data = await asyncio.wait_for(
            gateway.json(
                "intake",
                user=user,
                schema=_IntakeDraft,
                purpose="intake",
                images=pictures or None,
            ),
            settings.intake_timeout_seconds,
        )
        return _IntakeDraft.model_validate(data)

    sighted = bool(images)
    try:
        draft = await ask(images)
    except CanaryLeak:
        raise
    except Exception:
        # A writer that cannot see refuses the pictures: say so, and read the texts on their own.
        for source in image_sources:
            source.status, source.note = "failed", NO_SIGHT
        sighted = False
        if not texts or not images:
            if not images:
                for source in sources:
                    if source.status == "read":
                        source.status, source.note = (
                            "failed",
                            "It could not be read in time.",
                        )
            return result
        try:
            draft = await ask([])
        except CanaryLeak:
            raise
        except Exception:
            for source in sources:
                if source.status == "read":
                    source.status, source.note = (
                        "failed",
                        "It could not be read in time.",
                    )
            return result

    haystack = _key(
        " ".join([message, *(f"{block['title']} {block['text']}" for block in texts)])
    )
    applied = _apply(
        draft,
        context,
        haystack=haystack,
        sighted=sighted,
        today=today,
        settings=settings,
    )
    result.filled, result.places, result.wishes = (
        applied.filled,
        applied.places,
        applied.wishes,
    )
    return result


def shares_something(
    message: str, attachments: Sequence[Attachment], settings: Settings
) -> bool:
    return bool(settings.enable_intake and (attachments or find_urls(message, 1)))


__all__ = [
    "Attachment",
    "IntakeResult",
    "IntakeSource",
    "LINK_WORDS",
    "find_urls",
    "run_intake",
    "shares_something",
    "without_links",
]
