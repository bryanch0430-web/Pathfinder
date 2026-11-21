"""HTTP and SSE interface. No business logic lives here."""

from __future__ import annotations

import asyncio
import hmac
import html
import base64
import binascii
import json
import logging
import re
from contextlib import aclosing, asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints, ValidationError, field_validator

from app.application.intake import Attachment
from app.application.airport_transfers import sync_session_transfers
from app.application.plan_import import clean_imported_plan, schema_version
from app.application.planner import PACE_BUFFER_MINUTES, _pace
from app.application.transfers import refresh_transfers
from app.application.calendar_export import calendar_file_name, plan_calendar
from app.application.choices import (
    ChoiceNotFound,
    ChoiceRefused,
    apply_choice,
    is_chosen,
    locked_conflict,
    normalise_hotel_options,
    set_booked,
    set_lock,
)
from app.application.legs import leg_options_for_plan, legs_for_plan
from app.application.limits import turn_deadline_for
from app.application.toolbox import Toolbox
from app.infrastructure.observability import NoopTracer
from app.infrastructure.llm_providers import server_deepseek_key
from app.infrastructure.store import PlanConflict
from app.application.llm_selection import take_key_notice
from app.application.orchestrator import restore_message_decisions, user_facing_error
from app.application.prompts import LANGUAGE_RULE, PERSONA_RULE
from app.application.escalation import agent_reason, is_stuck
from app.application.skills import prompt_entries, prompt_file
from app.application.preferences import derive_preferences
from app.application.guide_hybrid import retrieve_hybrid
from app.application.soft_guides import CORPUS_VERSION
from app.application.trending_rag import trending_report
from app.application.translation import SUPPORTED_LOCALES, LLMTranslator, TranslationService
from app.application.validation import tidy_data_gaps
from app.config import Settings, get_settings
from app.container import Container, build_container
from app.application.auth import create_token, decode_token, hash_password, verify_password
from app.domain.credentials import SERVER_OWNER
from app.domain.models import (
    DateRange,
    Feedback,
    fit_preference,
    Intent,
    Message,
    PREFERENCE_MAX_LENGTH,
    PreferenceProfile,
    PREFERENCES_MAX,
    TRIP_FIELD_MAX_LENGTH,
    TRIP_LINE_MAX_LENGTH,
    TRIP_LINES_MAX,
    TRIP_STOPS_MAX,
    new_id,
    TripContext,
    TripPlan,
    TripStop,
    User,
    USER_PREFIX,
)
from app.evaluation import export_markdown
from app.interface.body_limit import BodyLimit
from app.interface.cache_control import PrivateResponses
from app.interface.caller_binding import CallerBinding
from app.interface.identity_format import TOKEN_FORMAT, TOKEN_MAX_LENGTH
from app.interface.llm_keys_api import register_llm_key_routes
from app.interface.serpapi_api import register_serpapi_routes
from app.interface.local_admin import local_admin_for
from app.infrastructure.providers.images import (
    CACHE_SECONDS,
    PHOTO_REF,
    PhotoUnavailable,
    fetch_place_photo,
)
from app.interface.rate_limit import RateLimits, SlidingWindowLimiter, rate_limit
from app.interface.sse import KEEPALIVE, KEEPALIVE_FRAME, TIMED_OUT, Deadline, paced, sse_frame

logger = logging.getLogger(__name__)

#: What a request may carry in its other free texts. Some are stored with a message or a rating and sent back with
#: the trip, so none may be longer than anything the page sends: an id (of a session or a stop), a locale tag (BCP 47
#: allows 35 characters), the label of a stop, the reason code of a rating.
ID_MAX_LENGTH = 80
LOCALE_MAX_LENGTH = 35
LABEL_MAX_LENGTH = 200
REASON_MAX_LENGTH = 80
#: How a lone surrogate reaches a JSON body: the escape of one half of a pair, or (from an encoder that does not
#: check) the character itself. `json.loads` reads UTF-8, UTF-16 and UTF-32 and lets both through, so the body
#: is searched as it is read and not as the bytes it was sent in.
_SURROGATE = re.compile(r"\\u[dD][89a-fA-F][0-9a-fA-F]{2}|[\ud800-\udfff]")
UNREADABLE_TEXT = "The request contains a character that cannot be read. Please retype that text."


def _attachments(sent: list["AttachmentIn"], settings: Settings) -> list[Attachment]:
    """The attached files as bytes. A file that is not base64, is too large, or is one too many is refused."""
    if not sent:
        return []
    if not settings.enable_intake or len(sent) > settings.intake_max_attachments:
        raise HTTPException(
            status_code=422, detail=f"At most {settings.intake_max_attachments} files can be attached to a message."
        )
    files: list[Attachment] = []
    for item in sent:
        # Four base64 characters carry three bytes: a file over the limit is refused before it is decoded.
        if len(item.data) > settings.intake_max_attachment_bytes * 4 // 3 + 4:
            raise HTTPException(status_code=413, detail=f"{item.name} is too large to attach.")
        try:
            data = base64.b64decode(item.data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"{item.name} could not be read as a file.") from exc
        files.append(Attachment(name=item.name, media_type=item.media_type, data=data))
    return files


async def refuse_unreadable_text(request: Request) -> None:
    """Refuse a JSON body with a lone surrogate (a broken input method, a string cut inside an emoji).

    Python reads such an escape into a string that cannot be written as UTF-8, so it would fail wherever the
    text is stored or sent back, with a 500. Two halves that make up one character are fine.
    """
    if request.method not in {"POST", "PUT", "PATCH"} or "json" not in request.headers.get("content-type", "").lower():
        return
    body = await request.body()
    try:
        text = body.decode(json.detect_encoding(body), "surrogatepass")  # the way json.loads reads the bytes
    except UnicodeDecodeError:
        return  # not text that can be read: the route says so
    if not _SURROGATE.search(text):
        return
    try:
        parsed = json.loads(body)
    except (ValueError, RecursionError):
        return  # not JSON that can be read: the route says so
    try:
        json.dumps(parsed, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError:
        raise HTTPException(status_code=422, detail=UNREADABLE_TEXT) from None


class CreateSessionRequest(BaseModel):
    client_token: str = Field(default="", max_length=TOKEN_MAX_LENGTH)


#: One text of the trip form, and one line of its lists. A request over the limits is refused, not cut; what
#: the system writes into the form is cut to them (`TripContext.fit_to_limits`), so the page can send it back.
FormText = Annotated[str, StringConstraints(max_length=TRIP_FIELD_MAX_LENGTH)]
FormLine = Annotated[str, StringConstraints(max_length=TRIP_LINE_MAX_LENGTH)]


class ContextPatch(BaseModel):
    destination: FormText | None = None
    origin: FormText | None = None
    start_date: date | None = None
    end_date: date | None = None
    days: int | None = Field(default=None, ge=1, le=60)
    party_size: int | None = Field(default=None, ge=1, le=20)
    budget: float | None = Field(default=None, ge=0)
    currency: FormText | None = None
    hotel_style: FormText | None = None
    pace: FormText | None = None
    #: The rest of the traveller profile: children, business or leisure, step-free access.
    with_children: bool | None = None
    purpose: Literal["leisure", "business"] | None = None
    step_free: bool | None = None
    preferences: list[FormLine] | None = Field(default=None, max_length=TRIP_LINES_MAX)
    hard_constraints: list[FormLine] | None = Field(default=None, max_length=TRIP_LINES_MAX)
    language: FormText | None = None
    plan_locale: Literal["en", "zh-Hant", "zh-Hans"] | None = None
    #: The cities in travel order ([] for one city); more than a trip holds is refused.
    stops: list[TripStop] | None = Field(default=None, max_length=TRIP_STOPS_MAX)
    one_way: bool | None = None


class AttachmentIn(BaseModel):
    """A file attached to a chat message: a PDF or a picture, its bytes as base64 (no ``data:`` prefix)."""

    name: str = Field(default="attachment", max_length=200)
    media_type: str = Field(default="", max_length=100)
    data: str = Field(min_length=1)


class DeskSay(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class DeskNoteIn(BaseModel):
    note: str = Field(default="", max_length=2000)


class DeskRunIn(BaseModel):
    """A last-resort instruction. It runs the same path a traveller message would, while the chat is held."""

    message: str = Field(min_length=1, max_length=6000)


class DeskBriefIn(BaseModel):
    """The trip form a person on the desk fills in. Saving it does not start a plan."""

    destination: str = Field(min_length=1, max_length=80)
    origin: str = Field(min_length=1, max_length=80)
    start_date: date
    end_date: date
    party_size: int = Field(ge=1, le=20)
    budget: float = Field(ge=0)
    currency: str = Field(default="HKD", min_length=3, max_length=8)
    preferences: list[str] = Field(default_factory=list, max_length=20)


class DeskStopIn(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    name: str = Field(default="", max_length=200)
    start: str = ""
    end: str = ""
    remove: bool = False


class DeskPlanIn(BaseModel):
    version: int
    items: list[DeskStopIn] = Field(default_factory=list, max_length=80)


class DeskEditIn(BaseModel):
    """One change for the edit-itinerary skill. ``item_id`` is the stop "this" refers to."""

    instruction: str = Field(min_length=1, max_length=2000)
    item_id: str = Field(default="", max_length=80)


class RowIn(BaseModel):
    text: str = ""
    score: int = Field(default=60, ge=0, le=100)


class MonitorIn(BaseModel):
    """One of the three monitors. Its keywords, examples, and engine are not shared."""

    enabled: bool = True
    engine: str = "keywords"
    words: list[RowIn] = Field(default_factory=list, max_length=80)
    examples: list[RowIn] = Field(default_factory=list, max_length=40)
    score: int | None = Field(default=None, ge=0, le=100)
    message: str = Field(default="", max_length=2000)
    after: int = Field(default=3, ge=2, le=8)


class MonitorsIn(BaseModel):
    hand: MonitorIn
    sentiment: MonitorIn
    stuck: MonitorIn


class EscalationIn(BaseModel):
    enabled: bool = True
    message: str = Field(default="", max_length=2000)
    frustration_at: int | None = Field(default=None, ge=0, le=100)
    examples: list[str] = Field(default_factory=list, max_length=40)
    match_mode: Literal["examples", "keywords"] = "examples"
    use_jev: bool = False
    stuck_on: bool = False
    stuck_after: int = Field(default=3, ge=2, le=8)
    words: list[str] = Field(default_factory=list, max_length=80)
    api_key: str = ""
    clear_key: bool = False
    monitors: MonitorsIn | None = None


class PromptIn(BaseModel):
    body: str = Field(default="", max_length=20000)


class OperatorCaseIn(BaseModel):
    """A failure an operator records. `session_id` ties it to a live or finished chat."""

    message: str = Field(min_length=1, max_length=500)
    expected: str = Field(default="", max_length=400)
    note: str = Field(default="", max_length=400)
    session_id: str = Field(default="", max_length=80)
    session_state: str = Field(default="", max_length=20)


class SuggestionIn(BaseModel):
    """The skill text or number an admin accepts. Empty keeps the writer's draft."""

    body: str = Field(default="", max_length=12000)


class OrchestratorIn(BaseModel):
    """One orchestrator model. An empty key leaves the saved key; ``clear_key`` restores the environment key."""

    model: str = ""
    url: str = ""
    api_key: str = ""
    clear_key: bool = False


class PoeKeyIn(BaseModel):
    """The Poe key shared by the two cloud gate choices. Blank keeps the saved key."""

    api_key: str = ""
    clear_key: bool = False


class GateArmIn(BaseModel):
    """One cloud gate: provider, model, key, and the call settings the request can send."""

    provider: str = ""
    model: str = ""
    base_url: str = ""
    api_key: str = ""
    clear_key: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning: str = ""


class WriterCallIn(BaseModel):
    """Call settings for the writer. The key stays on the key card; this is temperature, length, and reasoning."""

    temperature: float | None = None
    max_tokens: int | None = None
    reasoning: str = ""


class ReviewerIn(GateArmIn):
    """The reviewer: the same fields as a gate arm, plus how many rule checks run."""

    rounds: int | None = None


class RedactionIn(BaseModel):
    hkid: bool = True
    hk_phone: bool = True
    email: bool = True
    card: bool = True
    hkid_pattern: str = Field(default="", max_length=300)
    hk_phone_pattern: str = Field(default="", max_length=300)
    email_pattern: str = Field(default="", max_length=300)
    card_pattern: str = Field(default="", max_length=300)


class RedactionPreviewIn(RedactionIn):
    text: str = Field(default="", max_length=2000)


class RuntimeIn(BaseModel):
    thresholds: dict[str, float] | None = None
    freshness_hours: dict[str, int] | None = None
    #: ``jev`` is the decision model. ``plain`` is a fast model with reasoning off.
    #: ``reason`` is a fast model with reasoning on.
    gate: Literal["jev", "plain", "reason"] | None = None
    plain_model: str | None = None
    reason_model: str | None = None
    writer_model: str | None = None
    jev: OrchestratorIn | None = None
    fast: OrchestratorIn | None = None
    poe: PoeKeyIn | None = None
    plain: GateArmIn | None = None
    reason: GateArmIn | None = None
    reviewer: ReviewerIn | None = None
    writer_call: WriterCallIn | None = None
    redaction: RedactionIn | None = None


_PROMPT_NAMES = {"persona", "language"}


class ChatRequest(BaseModel):
    session_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)
    message: str = Field(min_length=1, max_length=6000)
    locale: str = Field(default="en", max_length=LOCALE_MAX_LENGTH)
    client_token: str = Field(default="", max_length=TOKEN_MAX_LENGTH)
    scope_item_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)
    #: human-readable label for the selected stop ("Day 1 · HK City Sightseeing"),
    #: stored with the message so the transcript keeps the context; a longer one is cut, not refused
    scope_label: str | None = None
    #: When true, the trace event includes each model's reasoning text.
    developer: bool = False
    #: When true, an edit is listed in the chat and written only after the traveller accepts it.
    review: bool = False
    #: PDFs or pictures to read with the message (the intake step); more than the limit is refused.
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=5)

    @field_validator("scope_label")
    @classmethod
    def _cut_label(cls, value: str | None) -> str | None:
        return value[:LABEL_MAX_LENGTH] if value else value


class ProfilePatch(BaseModel):
    #: guest token; may be empty when a bearer identifies the caller
    client_token: str = Field(default="", max_length=TOKEN_MAX_LENGTH)
    #: What a profile keeps (see `PreferenceProfile`). A request over either limit is refused, not cut, and the
    #: Settings page shows both; what the system itself writes is cut to them, so it can always be sent back.
    preferences: list[Annotated[str, StringConstraints(max_length=PREFERENCE_MAX_LENGTH)]] = Field(
        default_factory=list, max_length=PREFERENCES_MAX
    )


class ClearMemoryRequest(BaseModel):
    client_token: str = Field(default="", max_length=TOKEN_MAX_LENGTH)


class AuthRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(default="", max_length=80)
    client_token: str = Field(default="", max_length=TOKEN_MAX_LENGTH)
    session_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)


class ClaimRequest(BaseModel):
    session_id: str = Field(max_length=ID_MAX_LENGTH)
    client_token: str = Field(default="", max_length=TOKEN_MAX_LENGTH)


class FeedbackRequest(BaseModel):
    useful: bool
    stars: int | None = Field(default=None, ge=1, le=5)
    reason_code: str = Field(default="", max_length=REASON_MAX_LENGTH)
    client_token: str = Field(default="", max_length=TOKEN_MAX_LENGTH)


class LayoutMove(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    day: int = Field(ge=1, le=60)
    start: str = Field(min_length=4, max_length=5)
    end: str = Field(min_length=4, max_length=5)
    name: str = Field(default="", max_length=200)


class LayoutRequest(BaseModel):
    """The stops the direct-edit grid moved or retimed, and the plan version the grid was showing."""

    items: list[LayoutMove] = Field(max_length=200)
    drops: list[str] = Field(default_factory=list, max_length=200)
    plan_version: int | None = Field(default=None, ge=1)


class ProposalDecision(BaseModel):
    """Accept or reject one change the last edit offered."""

    change_id: str = Field(min_length=1, max_length=80)
    accept: bool


class ChoiceRequest(BaseModel):
    """Pick another option shown on the plan: a stop's alternative, a hotel option or a flight option."""

    kind: Literal["item", "hotel", "flight", "transport"]
    target_id: str | None = Field(default=None, max_length=ID_MAX_LENGTH)
    option_id: str = Field(max_length=ID_MAX_LENGTH)
    #: The plan version the traveller chose on; a choice made on an older version is refused (409).
    plan_version: int | None = Field(default=None, ge=1)
    #: Lock the chosen stay or leg in the same step (only a stay or a leg can be locked).
    lock: bool = False


class LockRequest(BaseModel):
    """Lock or unlock a chosen stay or leg without choosing anything."""

    kind: Literal["hotel", "transport"]
    target_id: str = Field(max_length=ID_MAX_LENGTH)
    locked: bool
    plan_version: int | None = Field(default=None, ge=1)


class BookedRequest(BaseModel):
    """Mark a locked stay or leg as booked by the traveller, or not booked."""

    kind: Literal["hotel", "transport"]
    target_id: str = Field(max_length=ID_MAX_LENGTH)
    booked: bool
    plan_version: int | None = Field(default=None, ge=1)


class RestoreRequest(BaseModel):
    """Make a saved version the current plan by copying it as the next version."""

    version: int = Field(ge=1)


class ImportPlanRequest(BaseModel):
    """A plan file from another computer. The plan is the JSON this app saves, or {"plan": that JSON}."""

    client_token: str = Field(default="", max_length=TOKEN_MAX_LENGTH)
    plan: dict[str, Any]


class EvalRunRequest(BaseModel):
    dataset_id: str = Field(max_length=ID_MAX_LENGTH)
    experiment_tag: str = Field(default="baseline-v1", max_length=ID_MAX_LENGTH)
    split: str = Field(default="all", max_length=ID_MAX_LENGTH)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.check_secrets()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.container = build_container(settings)
        yield

    app = FastAPI(
        title="Pathfinder",
        version="1.0.0",
        description="Pathfinder — agentic trip planner. English first.",
        lifespan=lifespan,
        dependencies=[Depends(refuse_unreadable_text)],
    )
    app.add_middleware(BodyLimit, limits={"/api/turn/stream": settings.intake_max_body_bytes})
    app.add_middleware(PrivateResponses)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Outermost, so the whole request (a streamed answer and its child tasks included) knows its caller.
    app.add_middleware(CallerBinding, settings=settings)
    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        """FastAPI's own answer, which repeats the value that was refused (`input`). A body that is not text cannot
        be written back, which made the answer itself fail with a 500: there, where and why is all it says."""
        errors = exc.errors()
        try:
            detail = jsonable_encoder(errors)
        except UnicodeDecodeError:
            detail = jsonable_encoder([{key: error[key] for key in ("loc", "msg", "type") if key in error} for error in errors])
        return JSONResponse(status_code=422, content={"detail": detail})

    # Per app instance, so every app (and every test app) starts with empty counters.
    app.state.rate_limits = RateLimits(
        auth=SlidingWindowLimiter(settings.rate_limit_auth_per_minute),
        ai=SlidingWindowLimiter(settings.rate_limit_ai_per_minute),
        create=SlidingWindowLimiter(settings.rate_limit_create_per_minute),
        lookup=SlidingWindowLimiter(settings.rate_limit_lookup_per_minute),
        handbook=SlidingWindowLimiter(settings.rate_limit_handbook_per_minute),
        key_test=SlidingWindowLimiter(settings.rate_limit_key_test_per_minute),
        edit=SlidingWindowLimiter(settings.rate_limit_edit_per_minute),
    )
    limit_auth = Depends(rate_limit("auth", "Too many sign-in attempts."))
    charge_ai = rate_limit("ai", "Too many planning requests.")
    limit_ai = Depends(charge_ai)
    # A turn, a new trip and a look-up are charged inside the route, once the request is known to do its
    # work, so one that is refused (401, 404, 422) costs nothing and cannot lock out the people it is not from.
    charge_create = rate_limit("create", "Too many new trips or share links.")
    charge_lookup = rate_limit("lookup", "Too many place look-ups.")
    charge_handbook = rate_limit("handbook", "Too many handbook look-ups.")
    charge_key_test = rate_limit("key_test", "Too many key tests.")
    charge_edit = rate_limit("edit", "Too many edits.")

    def container(request: Request) -> Container:
        return request.app.state.container

    def require_admin(request: Request, x_admin_token: str = Header(default="")) -> None:
        # Local admin mode: a caller on the owner's own machine needs no token. A token that is sent is
        # still checked, so a wrong one is refused there too.
        if not x_admin_token and local_admin_for(request, settings):
            return
        expected = settings.admin_token
        # An empty setting disables the admin endpoints instead of matching "".
        if not expected or not x_admin_token or not hmac.compare_digest(
            x_admin_token.encode("utf-8"), expected.encode("utf-8")
        ):
            raise HTTPException(status_code=401, detail="Admin token required")

    # -- meta -------------------------------------------------------------

    @app.get("/api/ready")
    async def health(request: Request) -> dict[str, Any]:
        # `local_admin`: this caller opens the admin pages without a token (and the page offers no sign-in).
        return {**container(request).health(), "local_admin": local_admin_for(request, settings)}

    @app.get("/api/runtime")
    async def config(request: Request) -> dict[str, Any]:
        c = container(request)
        writer = c.selector.operator_chain()
        return {
            "app": settings.app_name,
            "version": "1.0.0",
            "default_locale": settings.default_locale,
            "locales": settings.supported_locales.split(","),
            "currency": settings.default_currency,
            **_runtime_view(settings, c.store),
            # The operator chain, as /api/ready reports it: never the calling traveller's own key.
            "writer": {
                "available": bool(getattr(writer.llm, "available", False)),
                "model": getattr(writer.llm, "name", "offline"),
                "base_url": settings.writer_base_url,
                "label": settings.llm_provider,
                "vendor": settings.writer_vendor,
                "source": writer.source,
                "temperature": settings.llm_temperature,
                "max_tokens": settings.llm_max_tokens,
                "reasoning": settings.writer_reasoning,
            },
            "providers": c.registry.describe(),
            # Browser map keys are public by design (restrict them by referrer in
            # the cloud consoles). Nothing else from the environment is exposed.
            "maps": {
                "google": {
                    "key": settings.google_maps_api_key,
                    "enabled": bool(settings.google_maps_api_key),
                },
            },
            "demo_scripts": [
                "Plan a 3-day trip to Hong Kong from Singapore for 2 people in March, budget 9000 HKD.",
                "Swap the hotel for a cheaper option.",
                "What is the weather on day 2?",
            ],
        }

    @app.get("/api/wallet")
    async def fx_rates(
        request: Request,
        base: str = Query(default="HKD", min_length=3, max_length=3),
    ) -> dict[str, Any]:
        c = container(request)
        await c.registry.fx.refresh()
        return c.registry.fx.snapshot(base.upper())

    @app.get("/api/wallet/convert")
    async def fx_convert(
        request: Request,
        amount: float = Query(default=1.0, ge=0),
        from_currency: str = Query(default="HKD", alias="from", min_length=3, max_length=3),
        to_currency: str = Query(default="USD", alias="to", min_length=3, max_length=3),
    ) -> dict[str, Any]:
        c = container(request)
        await c.registry.fx.refresh()
        source = from_currency.upper()
        target = to_currency.upper()
        converted = c.registry.fx.convert(amount, source, target)
        rate = c.registry.fx.convert(1.0, source, target)
        return {
            "amount": amount,
            "from": source,
            "to": target,
            "converted": round(converted, 2),
            "rate": round(rate, 6),
            "source": c.registry.fx.source,
            "as_of": c.registry.fx.as_of.isoformat(),
            "note": c.registry.fx.note(),
        }

    # -- auth -------------------------------------------------------------

    def current_user(request: Request, c) -> User | None:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            return None
        payload = decode_token(header[7:].strip(), settings.auth_secret)
        if not payload:
            return None
        return c.store.get_user(str(payload.get("sub", "")))

    def checked_user(request: Request, c, client_token: str) -> User | None:
        """The bearer's user, refusing a `user:` token that is not the bearer's own."""
        user = current_user(request, c)
        if client_token.strip().startswith(USER_PREFIX):
            if user is None or client_token.strip() != f"{USER_PREFIX}{user.id}":
                raise HTTPException(status_code=401, detail="Sign in to use this profile.")
        return user

    def session_caller(request: Request, c, client_token: str) -> tuple[str, User | None]:
        """The guest token and the account a new session is made for.

        The token is `client_token`, else the `X-Client-Token` header. One that could not be presented
        again is 422: the session would stay in the database and open for nobody. A signed-in caller's
        trip carries the account's own profile token, whatever guest token it names (so it cannot add
        to another guest's profile), and that token is not checked: it is not the one that is stored.
        """
        token = client_token or request.headers.get("x-client-token", "").strip()
        user = checked_user(request, c, token)
        if user is not None:
            return f"{USER_PREFIX}{user.id}", user
        if token and not TOKEN_FORMAT.fullmatch(token):
            raise HTTPException(
                status_code=422,
                detail=f"The guest token must be 1 to {TOKEN_MAX_LENGTH} printable ASCII characters without spaces.",
            )
        return token, None

    def presented_token(request: Request, client_token: str = "") -> str:
        """The guest token a caller presents: an explicit `client_token` (older clients), else `X-Client-Token`.

        The header is the way to send it: a query string is written to server and proxy logs.
        """
        return (client_token or "").strip() or request.headers.get("x-client-token", "").strip()

    def resolve_identity(request: Request, c, client_token: str = "") -> str:
        """The one identity that owns a caller's profile, saved trips and memory.

        A valid bearer is `user:<id>`; otherwise the guest token (see `presented_token`). A
        `user:` token without that user's bearer, or no identity at all, is 401.
        """
        token = presented_token(request, client_token)
        user = checked_user(request, c, token)
        if user is not None:
            return f"{USER_PREFIX}{user.id}"
        if token:
            return token
        raise HTTPException(status_code=401, detail="Sign in or send a client token.")

    def adopt_guest_memory(c, client_token: str, profile_token: str) -> None:
        """On sign-in, move a guest's preferences and saved trips to the account.

        Only guest tokens are adopted: a `user:` token here would let one
        account absorb another account's memory.
        """
        guest = (client_token or "").strip()
        if not guest or guest == profile_token or guest.startswith(USER_PREFIX):
            return
        c.store.merge_preferences(guest, profile_token)
        # "server" names the operator's own key, never a guest's: it is not handed to an account.
        if guest != SERVER_OWNER:
            c.store.adopt_credentials(guest, profile_token)
        c.store.reassign_saved_trips(guest, profile_token)

    def require_caller(request: Request) -> None:
        """A cost endpoint answers a caller who has an identity: a valid bearer or a guest token."""
        if request.headers.get("x-client-token", "").strip():
            return
        if current_user(request, container(request)) is None:
            raise HTTPException(status_code=401, detail="Sign in or send a client token.")

    def same_token(presented: str, expected: str) -> bool:
        """True when both guest tokens are non-empty and equal (constant-time compare)."""
        if not presented or not expected:
            return False
        return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))

    def adopt_guest_sessions(c, client_token: str, user: User) -> None:
        """On sign-in, move every guest session this browser made to the account.

        Holding the guest token is the proof, for all the sessions made with it: after sign-in the
        page sends the account's identity instead, so a session left behind would stop opening.
        Knowing a session id alone takes nothing over. A ``user:`` token names an account's
        profile and is no guest's proof, and an empty token proves nothing.
        """
        if not client_token or client_token.startswith(USER_PREFIX):
            return
        c.store.adopt_sessions(client_token, user.id)

    def auth_response(c, user: User) -> dict[str, Any]:
        token = create_token(user.id, user.email, settings.auth_secret, settings.auth_token_hours)
        profile_token = f"user:{user.id}"
        return {
            "token": token,
            "token_type": "bearer",
            "profile_token": profile_token,
            "user": user.model_dump(mode="json"),
        }

    @app.get("/api/places/suggest", dependencies=[Depends(require_caller)])
    async def place_suggestions(
        request: Request,
        q: str = Query(min_length=3, max_length=80),
        limit: int = Query(default=5, ge=1, le=10),
    ) -> dict[str, Any]:
        """Destination autocomplete (Places API New). Needs a bearer or a client token; no secrets returned."""
        await charge_lookup(request)
        c = container(request)
        provider = c.registry.autocomplete
        if provider is None or not getattr(provider, "available", False):
            return {"items": []}
        try:
            items = await provider.suggest(q, limit)
        except Exception:
            items = []
        return {"items": items}

    @app.get("/api/images/place")
    async def place_image(request: Request, ref: str = Query(pattern=PHOTO_REF.pattern, max_length=2048)) -> Response:
        """A Google place photo, fetched with the server's key and kept on disk.

        A page's <img> cannot send the identity headers, so the route is open; what keeps it from being a way to
        spend the key is that only a photo resource name is accepted (anything else is refused with 422, so no other
        address can be fetched) and that it shares the look-up rate limit."""
        await charge_lookup(request)
        c = container(request)
        if not c.settings.google_places_api_key:
            raise HTTPException(status_code=404, detail="No place photos are available.")
        try:
            data, content_type = await fetch_place_photo(ref, c.settings)
        except PhotoUnavailable:
            raise HTTPException(status_code=502, detail="The photo could not be fetched.") from None
        return Response(
            content=data,
            media_type=content_type,
            headers={"Cache-Control": f"public, max-age={CACHE_SECONDS}"},
        )

    @app.get("/api/handbook", dependencies=[Depends(require_caller)])
    async def guides(
        request: Request,
        destination: str = Query(min_length=2, max_length=80),
        q: str = Query(default="", max_length=200),
        limit: int = Query(default=3, ge=1, le=6),
        rerank: bool = Query(default=False),
    ) -> dict[str, Any]:
        await charge_handbook(request)
        items, retrieval = await asyncio.to_thread(retrieve_hybrid, destination, q, limit, rerank)
        return {
            "items": items,
            "corpus_version": CORPUS_VERSION,
            "retrieval": retrieval,
            "note": "Hybrid BM25 + BGE-small, then BGE reranker. Soft context only; never a price, opening hour or booking claim.",
        }

    @app.post("/api/account/signup", dependencies=[limit_auth])
    async def register(payload: AuthRequest, request: Request) -> dict[str, Any]:
        c = container(request)
        email = payload.email.strip().lower()
        if "@" not in email or "." not in email.split("@")[-1]:
            raise HTTPException(status_code=422, detail="Enter a valid email address.")
        if c.store.get_user_by_email(email):
            raise HTTPException(status_code=409, detail="An account with that email already exists.")
        user = User(email=email, display_name=payload.display_name.strip() or email.split("@")[0])
        c.store.create_user(user, hash_password(payload.password))
        profile_token = f"{USER_PREFIX}{user.id}"
        adopt_guest_memory(c, payload.client_token, profile_token)
        adopt_guest_sessions(c, payload.client_token, user)
        return auth_response(c, user)

    @app.post("/api/account/signin", dependencies=[limit_auth])
    async def login(payload: AuthRequest, request: Request) -> dict[str, Any]:
        c = container(request)
        email = payload.email.strip().lower()
        found = c.store.get_user_by_email(email)
        if not found or not verify_password(payload.password, found[1]):
            raise HTTPException(status_code=401, detail="Email or password is incorrect.")
        user = found[0]
        profile_token = f"{USER_PREFIX}{user.id}"
        adopt_guest_memory(c, payload.client_token, profile_token)
        adopt_guest_sessions(c, payload.client_token, user)
        return auth_response(c, user)

    @app.get("/api/account/whoami")
    async def me(request: Request) -> dict[str, Any]:
        c = container(request)
        user = current_user(request, c)
        if user is None:
            raise HTTPException(status_code=401, detail="Not signed in.")
        return {"user": user.model_dump(mode="json"), "profile_token": f"user:{user.id}"}

    @app.post("/api/account/attach")
    async def claim(payload: ClaimRequest, request: Request) -> dict[str, Any]:
        c = container(request)
        user = current_user(request, c)
        if user is None:
            raise HTTPException(status_code=401, detail="Sign in before claiming a session.")
        session = c.store.get_session(payload.session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.user_id:
            if session.user_id != user.id:
                raise HTTPException(status_code=404, detail="Session not found")
        elif not session.owner or not same_token(payload.client_token, session.client_token):
            # Knowing a guest session's id is not enough to take it over, and a `user:` token that no
            # bearer proved owns nothing (`Session.owner`), as in `require_session`.
            raise HTTPException(status_code=404, detail="Session not found")
        c.store.set_session_user(session.id, user.id)
        return {"claimed": True, "session_id": session.id}

    def require_session(c, request: Request, session_id: str):
        """The caller's own session, or 404 (a session that is not yours looks like one that is not there).

        An account's session needs that account's bearer. A guest session needs
        the guest token it was created with, sent as `X-Client-Token`; a `user:` token on a
        row with no account was never proven with a bearer, so it opens nothing (`Session.owner`).
        """
        session = c.store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.user_id:
            user = current_user(request, c)
            if user is None or user.id != session.user_id:
                raise HTTPException(status_code=404, detail="Session not found")
        elif not session.owner or not same_token(request.headers.get("x-client-token", ""), session.client_token):
            raise HTTPException(status_code=404, detail="Session not found")
        return session

    # -- sessions ---------------------------------------------------------

    @app.post("/api/journeys")
    async def create_session(payload: CreateSessionRequest, request: Request) -> dict[str, Any]:
        c = container(request)
        token, user = session_caller(request, c, payload.client_token)
        if not token and user is None:
            # A trip nobody could open again would stay in the database for good.
            raise HTTPException(status_code=401, detail="Sign in or send a client token.")
        await charge_create(request)
        session = c.store.create_session(token, user.id if user else "")
        context = TripContext(
            session_id=session.id,
            language=settings.default_locale,
            party_size=None,
            currency=settings.default_currency,
        )
        c.store.save_context(context)
        return {
            "session": session.model_dump(mode="json", exclude={"client_token"}),
            "context": context.model_dump(mode="json"),
        }

    @app.get("/api/journeys/{session_id}")
    async def get_session(session_id: str, request: Request) -> dict[str, Any]:
        c = container(request)
        session = require_session(c, request, session_id)
        c.store.mark_seen(session_id)
        plan = c.store.get_plan(session_id)
        if plan is not None:
            # Proposal DB-13: a reopened plan has its outdated sections re-fetched.
            plan = await c.turn_service.freshness.on_reopen(plan)
            plan = tidy_data_gaps(plan, drop_edit_gaps=True)
        return {
            "session": session.model_dump(mode="json", exclude={"client_token"}),
            "context": (c.store.get_context(session_id) or TripContext(session_id=session_id)).model_dump(mode="json"),
            "plan": plan.model_dump(mode="json") if plan else None,
            "versions": c.store.list_versions(session_id),
            "messages": [
                message.model_dump(mode="json")
                for message in restore_message_decisions(
                    c.store, settings.trace_dir, c.store.list_messages(session_id, limit=60)
                )
            ],
            "held": c.store.is_held(session_id),
            "agent_name": c.store.held_agent(session_id),
            "planner_busy": c.turn_service.session_busy(session_id),
            "planner_note": c.turn_service.session_activity(session_id),
        }

    @app.get("/api/journeys/{session_id}/held")
    async def journey_held(session_id: str, request: Request) -> dict[str, Any]:
        """Whether a person on the desk has this chat, and that person's account name."""
        c = container(request)
        require_session(c, request, session_id)
        c.store.mark_seen(session_id)
        return {
            "held": c.store.is_held(session_id),
            "agent_name": c.store.held_agent(session_id),
            "planner_busy": c.turn_service.session_busy(session_id),
            "planner_note": c.turn_service.session_activity(session_id),
        }

    @app.patch("/api/journeys/{session_id}/state")
    async def patch_context(session_id: str, payload: ContextPatch, request: Request) -> dict[str, Any]:
        c = container(request)
        require_session(c, request, session_id)
        context = c.store.get_context(session_id) or TripContext(session_id=session_id)
        was = (context.budget, context.currency.upper(), context.start_date)
        data = payload.model_dump(exclude_unset=True)
        # A field the form sent as null is being cleared. Omitting it leaves the stored value, which is
        # what made a cleared form come back with its old dates, party and budget.
        clearable = {"start_date", "end_date", "days", "party_size", "budget"}
        for key, value in data.items():
            if value is None and key not in clearable:
                continue
            if key in {"with_children", "purpose", "step_free"}:
                if value is not None:
                    context.profile = context.profile.model_copy(update={key: value})
            else:
                setattr(context, key, [TripStop.model_validate(row) for row in value] if key == "stops" else value)
        # "Plan anyway" was said of the budget and the start date the form had: other ones are checked again.
        if (context.budget, context.currency.upper()) != was[:2]:
            context.budget_confirmed = False
        if context.start_date != was[2]:
            context.past_dates_confirmed = False
        if context.start_date and context.end_date and context.end_date < context.start_date:
            raise HTTPException(status_code=422, detail="End date must not precede start date")
        if context.start_date and context.end_date:
            context.days = (context.end_date - context.start_date).days + 1
        elif context.start_date and context.days:
            from datetime import timedelta

            context.end_date = context.start_date + timedelta(days=context.days - 1)
        if context.currency:
            context.currency = context.currency.upper()
        c.store.save_context(context)
        return {"context": context.model_dump(mode="json")}

    @app.get("/api/journeys/{session_id}/itinerary")
    async def get_plan(session_id: str, request: Request, version: int | None = None) -> dict[str, Any]:
        c = container(request)
        require_session(c, request, session_id)
        plan = c.store.get_plan(session_id, version)
        if plan is None:
            raise HTTPException(status_code=404, detail="No plan for this session")
        if version is None:
            plan = await c.turn_service.freshness.on_reopen(plan)
        plan = tidy_data_gaps(plan, drop_edit_gaps=True)
        return {"plan": plan.model_dump(mode="json")}

    @app.post("/api/journeys/{session_id}/itinerary/restore")
    async def restore_plan(session_id: str, payload: RestoreRequest, request: Request) -> dict[str, Any]:
        """Copy a saved version forward so the next chat edits that plan, not the one that came after it."""
        from datetime import datetime, timezone

        c = container(request)
        require_session(c, request, session_id)
        if c.store.is_held(session_id):
            raise HTTPException(status_code=409, detail="An operator is editing this plan. You can edit it after they release the draft.")
        if c.turn_service.session_busy(session_id):
            raise HTTPException(status_code=409, detail=c.turn_service.session_activity(session_id) or "Pathfinder is changing this plan.")
        await charge_edit(request)
        current = c.store.get_plan(session_id)
        chosen = c.store.get_plan(session_id, payload.version)
        if current is None or chosen is None:
            raise HTTPException(status_code=404, detail="No plan for this session")
        if chosen.version == current.version:
            plan = tidy_data_gaps(current, drop_edit_gaps=True)
            return {"plan": plan.model_dump(mode="json")}
        restored = chosen.model_copy(deep=True)
        restored.version = current.version + 1
        restored.change_summary = f"Restored version {chosen.version}"
        restored.updated_at = datetime.now(timezone.utc)
        c.store.save_plan(restored, restored.change_summary)
        plan = tidy_data_gaps(restored, drop_edit_gaps=True)
        return {"plan": plan.model_dump(mode="json")}

    @app.post("/api/journeys/{session_id}/layout")
    async def apply_direct_edit(session_id: str, payload: LayoutRequest, request: Request) -> dict[str, Any]:
        """Write the grid's days and times onto the latest plan."""
        from app.application.proposals import apply_layout

        c = container(request)
        require_session(c, request, session_id)
        if c.store.is_held(session_id):
            raise HTTPException(status_code=409, detail="An operator is editing this plan. You can edit it after they release the draft.")
        if c.turn_service.session_busy(session_id):
            raise HTTPException(status_code=409, detail=c.turn_service.session_activity(session_id) or "Pathfinder is changing this plan.")
        await charge_edit(request)
        plan = c.store.get_plan(session_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="This trip has no plan.")
        if payload.plan_version is not None and payload.plan_version != plan.version:
            raise HTTPException(
                status_code=409,
                detail="This edit was made on an earlier version of the plan; open the latest version and edit again.",
            )
        try:
            updated = apply_layout(plan, [item.model_dump() for item in payload.items], payload.drops)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc) or "That edit does not fit this plan.") from None
        if updated is not plan:
            # The transfers between stops follow the stops that moved, and the airport transfers follow the stays.
            context = c.store.get_context(session_id)
            toolbox = Toolbox(path="modify", tracer=NoopTracer(), store=c.store, request_id=new_id("layout"))
            await refresh_transfers(
                plan,
                updated,
                toolbox=toolbox,
                source=getattr(c.registry, "transfers", None),
                buffer=PACE_BUFFER_MINUTES.get(_pace(context) if context is not None else "balanced", 5),
            )
            said = await sync_session_transfers(
                updated, registry=c.registry, store=c.store, session_id=session_id, before=plan
            )
            from app.application.airport_transfers import dropped_hand_edit

            lost = dropped_hand_edit(plan, updated, allowed=set(payload.drops))
            if lost:
                raise HTTPException(status_code=422, detail=lost)
            if said:
                updated.change_summary = " ".join([updated.change_summary, *said]).strip()
            try:
                c.store.save_plan(updated, updated.change_summary)
            except PlanConflict as exc:
                raise HTTPException(
                    status_code=409, detail="The plan changed meanwhile; reload it and edit again."
                ) from exc
        shown = tidy_data_gaps(updated, drop_edit_gaps=True)
        return {"plan": shown.model_dump(mode="json")}

    @app.post("/api/journeys/{session_id}/proposals/{message_id}")
    async def decide_proposal(
        session_id: str, message_id: str, payload: ProposalDecision, request: Request
    ) -> dict[str, Any]:
        """Write one offered change into the current plan, or leave the plan and mark that change rejected."""
        from app.application.proposals import WHOLE_CHANGES, apply_change, mark_decision

        c = container(request)
        require_session(c, request, session_id)
        await charge_edit(request)
        message = next(
            (row for row in c.store.list_messages(session_id, limit=200) if row.id == message_id),
            None,
        )
        if message is None:
            raise HTTPException(status_code=404, detail="That reply is not in this trip.")
        proposal = message.meta.get("proposal") if isinstance(message.meta, dict) else None
        changes = list((proposal or {}).get("changes") or [])
        change = next((row for row in changes if row.get("id") == payload.change_id), None)
        if change is None or change.get("status") != "pending":
            raise HTTPException(status_code=409, detail="That change is no longer waiting.")
        plan = c.store.get_plan(session_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="This trip has no plan.")
        if payload.accept:
            offered_on = (proposal or {}).get("base_version")
            if change.get("id") in WHOLE_CHANGES and offered_on is not None and offered_on != plan.version:
                raise HTTPException(status_code=409, detail="The plan changed since this was offered; ask again.")
            try:
                updated = apply_change(plan, change)
            except (ValueError, ValidationError):
                raise HTTPException(status_code=422, detail="That change no longer fits this plan.") from None
            clash = locked_conflict(plan, updated)
            if clash:
                raise HTTPException(
                    status_code=409,
                    detail=f"This would change {clash}, which is locked in. Press Change on it first.",
                )
            said = await sync_session_transfers(
                updated, registry=c.registry, store=c.store, session_id=session_id, before=plan
            )
            if said:
                updated.change_summary = " ".join([updated.change_summary, *said]).strip()
            context = c.store.get_context(session_id)
            if context is not None:
                try:
                    updated.cost = c.turn_service.modify_path.planner.cost_for_plan(context, updated)
                except ValueError:  # a price that cannot be worked out keeps the total the plan had
                    logger.warning("the cost of an accepted edit could not be worked out")
            try:
                c.store.save_plan(updated, updated.change_summary)
            except PlanConflict as exc:
                raise HTTPException(
                    status_code=409, detail="The plan changed meanwhile; reload it and choose again."
                ) from exc
            held_form = (proposal or {}).get("context")
            if held_form and change.get("id") in WHOLE_CHANGES:
                # The trip form follows the route the accepted edit made; a held edit never wrote it.
                form = c.store.get_context(session_id)
                if form is not None:
                    try:
                        held_context = TripContext.model_validate(held_form)
                    except ValidationError:
                        held_context = None
                    if held_context is not None:
                        form.stops, form.destination, form.one_way = (
                            held_context.stops,
                            held_context.destination,
                            held_context.one_way,
                        )
                        c.store.save_context(form)
        else:
            updated = plan
        mark_decision(changes, payload.change_id, payload.accept)
        verb = "Accepted" if payload.accept else "Rejected"
        prior = list((message.meta.get("log") or {}).get("steps") or [])
        message.meta["proposal"] = {"changes": changes}
        message.meta["log"] = {
            "ok": payload.accept,
            "reason": f"{verb}: {change.get('label') or 'a change'}",
            "steps": [*prior, f"{verb}: {change.get('label') or 'a change'}"][:8],
        }
        c.store.add_message(message)
        shown = tidy_data_gaps(updated, drop_edit_gaps=True)
        return {"plan": shown.model_dump(mode="json"), "message": message.model_dump(mode="json")}

    @app.post("/api/journeys/import")
    async def import_plan(payload: ImportPlanRequest, request: Request) -> dict[str, Any]:
        """Open a saved plan file as a new trip on this computer."""
        from datetime import datetime, timezone

        c = container(request)
        token, user = session_caller(request, c, payload.client_token)
        if not token and user is None:
            raise HTTPException(status_code=401, detail="Sign in or send a client token.")
        await charge_create(request)
        if payload.plan.get("schema_version") != schema_version():
            raise HTTPException(
                status_code=422,
                detail="That file was saved by a different version of Pathfinder and cannot be opened here.",
            )
        try:
            incoming = TripPlan.model_validate(payload.plan)
        except ValidationError:
            raise HTTPException(status_code=422, detail="That file is not a Pathfinder plan.") from None
        # A file is data from outside: no locks, no confirmations, web links only, derived data built here.
        clean_imported_plan(incoming)
        session = c.store.create_session(token, user.id if user else "")
        plan = incoming.model_copy(deep=True)
        plan.session_id = session.id
        plan.trip_id = new_id("trip")
        plan.version = 1
        plan.change_summary = "Imported plan"
        plan.updated_at = datetime.now(timezone.utc)
        context = TripContext(
            session_id=session.id,
            destination=plan.destination.name,
            origin=plan.origin,
            start_date=plan.dates.start,
            end_date=plan.dates.end,
            days=len(plan.dates.dates) or len(plan.days) or None,
            party_size=plan.party_size,
            budget=float(plan.budget.amount) if plan.budget is not None else None,
            currency=(plan.budget.currency if plan.budget is not None else plan.cost.currency) or settings.default_currency,
            language=settings.default_locale,
        )
        c.store.save_context(context)
        await sync_session_transfers(plan, registry=c.registry, store=c.store, session_id=session.id)
        c.store.save_plan(plan, plan.change_summary)
        plan = tidy_data_gaps(plan, drop_edit_gaps=True)
        return {
            "session": session.model_dump(mode="json", exclude={"client_token"}),
            "context": context.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
            "versions": c.store.list_versions(session.id),
            "messages": [],
        }

    # Display translation of a plan version; the English plan stays the source of truth.
    @app.get("/api/journeys/{session_id}/translation")
    async def get_translation(
        session_id: str, request: Request, version: int | None = None, locale: str | None = None
    ) -> dict[str, Any]:
        c = container(request)
        require_session(c, request, session_id)
        if locale is None:
            context = c.store.get_context(session_id)
            locale = getattr(context, "plan_locale", "en") if context is not None else "en"
        if locale not in SUPPORTED_LOCALES:
            raise HTTPException(status_code=422, detail=f"Unsupported locale; use one of {', '.join(SUPPORTED_LOCALES)}")
        plan = c.store.get_plan(session_id, version)
        if plan is None:
            raise HTTPException(status_code=404, detail="No plan for this session")
        if c.translation is None:
            c.translation = TranslationService(LLMTranslator(c.gateway))
        return await c.translation.translate_plan(tidy_data_gaps(plan, drop_edit_gaps=True), locale)

    @app.get("/api/journeys/{session_id}/revisions")
    async def get_versions(session_id: str, request: Request) -> dict[str, Any]:
        c = container(request)
        require_session(c, request, session_id)
        return {"versions": c.store.list_versions(session_id)}

    @app.get("/api/journeys/{session_id}/geometry")
    async def get_map(session_id: str, request: Request) -> dict[str, Any]:
        c = container(request)
        require_session(c, request, session_id)
        plan = c.store.get_plan(session_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="No plan for this session")
        return map_payload(plan)

    @app.get("/api/journeys/{session_id}/calendar.ics")
    async def get_calendar(session_id: str, request: Request) -> Response:
        """The latest plan as an iCalendar file: stops, stays and the chosen transport, in each city's time zone."""
        c = container(request)
        require_session(c, request, session_id)
        plan = c.store.get_plan(session_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="No plan for this session")
        return Response(
            content=plan_calendar(plan),
            media_type="text/calendar; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{calendar_file_name(plan)}"'},
        )

    @app.get("/api/journeys/{session_id}/segments")
    async def get_legs(session_id: str, request: Request, day: str | None = None) -> dict[str, Any]:
        c = container(request)
        require_session(c, request, session_id)
        plan = c.store.get_plan(session_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="No plan for this session")
        return await legs_for_plan(
            plan,
            routes=c.registry.routes,
            settings=settings,
            day=day,
        )

    @app.get("/api/journeys/{session_id}/leg-options")
    async def get_leg_options(
        session_id: str, request: Request, from_id: str, to_id: str
    ) -> dict[str, Any]:
        """Walk / cycle / transit / taxi for one leg, each with its route line."""
        c = container(request)
        require_session(c, request, session_id)
        plan = c.store.get_plan(session_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="No plan for this session")
        try:
            return await leg_options_for_plan(
                plan,
                routes=c.registry.routes,
                settings=settings,
                from_id=from_id,
                to_id=to_id,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Both stops must be in the current plan")

    @app.post("/api/journeys/{session_id}/plan/choose")
    async def choose_option(session_id: str, payload: ChoiceRequest, request: Request) -> dict[str, Any]:
        """Swap in the option the traveller picked, with no model call, and save it as the next plan version."""
        c = container(request)
        require_session(c, request, session_id)
        if c.store.is_held(session_id):
            raise HTTPException(status_code=409, detail="An operator is editing this plan. You can edit it after they release the draft.")
        if c.turn_service.session_busy(session_id):
            raise HTTPException(status_code=409, detail=c.turn_service.session_activity(session_id) or "Pathfinder is changing this plan.")
        plan = c.store.get_plan(session_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="No plan for this session")
        if payload.kind == "item" and not payload.target_id:
            raise HTTPException(status_code=422, detail="Choosing a stop's option needs the stop's target_id")
        if payload.lock and payload.kind == "item":
            raise HTTPException(status_code=422, detail="Only a stay or a leg can be locked.")
        if payload.plan_version is not None and payload.plan_version != plan.version:
            raise HTTPException(
                status_code=409,
                detail="This choice was made on an earlier version of the plan; open the latest version and choose again.",
            )
        if payload.lock and is_chosen(plan, payload.kind, payload.target_id, payload.option_id):
            # The option is already the chosen one: only the lock changes, on the version as it stands.
            try:
                target = payload.target_id or next(
                    (s.id for s in plan.hotels if s.id == payload.option_id), None
                ) or next((plan.leg_id_of(t) for t in plan.tickets if t.id == payload.option_id), None)
                set_lock(plan, payload.kind, target, True)
            except ChoiceNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc).capitalize()) from exc
            c.store.save_plan(plan, None, replace=True)
            return {"plan": tidy_data_gaps(plan, drop_edit_gaps=True).model_dump(mode="json")}
        # The day's transfers come from the transport model (no network), logged like the edit path's.
        toolbox = Toolbox(path="modify", tracer=NoopTracer(), store=c.store, request_id=new_id("choose"))
        try:
            updated = await apply_choice(
                plan,
                kind=payload.kind,
                target_id=payload.target_id,
                option_id=payload.option_id,
                planner=c.plan_path.planner,
                context=c.store.get_context(session_id),
                toolbox=toolbox,
                transfers=getattr(c.registry, "transfers", None),
            )
        except ChoiceNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc).capitalize()) from exc
        except ChoiceRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        said = await sync_session_transfers(
            updated, registry=c.registry, store=c.store, session_id=session_id, before=plan
        )
        if said:
            updated.change_summary = " ".join([updated.change_summary, *said]).strip()
        if payload.lock:
            option_leg = next((plan.leg_id_of(t) for t in plan.tickets if t.id == payload.option_id), None)
            set_lock(updated, payload.kind, payload.option_id if payload.kind == "hotel" else option_leg, True)
        try:
            c.store.save_plan(updated, updated.change_summary)
        except PlanConflict as exc:
            raise HTTPException(status_code=409, detail="The plan changed meanwhile; reload it and choose again.") from exc
        return {"plan": tidy_data_gaps(updated, drop_edit_gaps=True).model_dump(mode="json")}

    @app.post("/api/journeys/{session_id}/lock")
    async def lock_choice(session_id: str, payload: LockRequest, request: Request) -> dict[str, Any]:
        """Lock or unlock a chosen stay or leg on the latest version, in place: a lock is not a change of plan."""
        c = container(request)
        require_session(c, request, session_id)
        plan = c.store.get_plan(session_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="No plan for this session")
        if payload.plan_version is not None and payload.plan_version != plan.version:
            raise HTTPException(
                status_code=409,
                detail="This was done on an earlier version of the plan; open the latest version and try again.",
            )
        try:
            set_lock(plan, payload.kind, payload.target_id, payload.locked)
        except ChoiceNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc).capitalize()) from exc
        except ChoiceRefused as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        c.store.save_plan(plan, None, replace=True)
        return {"plan": tidy_data_gaps(plan, drop_edit_gaps=True).model_dump(mode="json")}

    @app.post("/api/journeys/{session_id}/booked")
    async def mark_booked(session_id: str, payload: BookedRequest, request: Request) -> dict[str, Any]:
        """Mark a locked stay or leg as booked, or not, on the latest version, in place: like a lock, it is plan data
        and not a change of plan. Only a locked one can be marked booked (422)."""
        c = container(request)
        require_session(c, request, session_id)
        plan = c.store.get_plan(session_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="No plan for this session")
        if payload.plan_version is not None and payload.plan_version != plan.version:
            raise HTTPException(
                status_code=409,
                detail="This was done on an earlier version of the plan; open the latest version and try again.",
            )
        try:
            set_booked(plan, payload.kind, payload.target_id, payload.booked)
        except ChoiceNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc).capitalize()) from exc
        except ChoiceRefused as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        c.store.save_plan(plan, None, replace=True)
        return {"plan": tidy_data_gaps(plan, drop_edit_gaps=True).model_dump(mode="json")}

    @app.get("/api/journeys/{session_id}/plan/hotels/{hotel_id}/rooms")
    async def hotel_rooms(session_id: str, hotel_id: str, request: Request) -> dict[str, Any]:
        """The rooms the hotel source lists for a stay, fetched once and kept on the plan."""
        c = container(request)
        require_session(c, request, session_id)
        plan = c.store.get_plan(session_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="No plan for this session")
        # The stay as it stands now leads the options; every stay is asked about the stay's own dates.
        normalise_hotel_options(plan)
        stays = [stay for stay in [*plan.hotels, *plan.hotel_options] if stay.id == hotel_id]
        if not stays:
            raise HTTPException(status_code=404, detail="Hotel not found")
        stay = stays[0]
        # Rooms are asked for the dates of the chosen stay the option stands in for.
        current = stay if stay in plan.hotels else (next(iter(plan.stays_in(stay.segment_id)), None) or plan.first_stay)
        check_in = current.check_in if current is not None else stay.check_in
        check_out = current.check_out if current is not None else stay.check_out
        wanted = DateRange(start=check_in, end=check_out)
        checked = stay.rooms_checked_for
        if checked is None and stay.room_types:
            # Rooms kept before the marker existed were fetched for the stay's own dates.
            checked = DateRange(start=stay.check_in, end=stay.check_out)
        if checked == wanted:
            # Asked before for these dates: the answer is kept, even when the source listed no room.
            return {"rooms": [room.model_dump(mode="json") for room in stay.room_types]}
        source = next(
            (item for item in c.registry.hotels if hasattr(item, "rooms") and getattr(item, "available", False)), None
        )
        rooms = []
        asked = False
        if source is not None and stay.property_token:
            try:
                rooms = await source.rooms(
                    property_token=stay.property_token,
                    name=stay.name,
                    check_in=check_in,
                    check_out=check_out,
                    party_size=plan.party_size,
                    currency=(stay.price_per_night.currency if stay.price_per_night else plan.cost.currency) or "USD",
                )
                asked = True
            except Exception:
                rooms = []
        if asked:
            for item in stays:
                item.room_types = list(rooms)
                item.rooms_checked_for = wanted
            try:
                # A cache on the version it was read from, not a change of plan.
                c.store.save_plan(plan, None, replace=True)
            except PlanConflict:
                pass
        return {"rooms": [room.model_dump(mode="json") for room in rooms]}

    @app.post("/api/journeys/{session_id}/finalise")
    async def finalise(session_id: str, request: Request) -> dict[str, Any]:
        """Freeze the plan. Nothing is written to preferences until the traveller rates it."""
        c = container(request)
        require_session(c, request, session_id)
        if c.store.get_plan(session_id) is None:
            raise HTTPException(status_code=404, detail="No plan for this session")
        context = c.store.get_context(session_id) or TripContext(session_id=session_id)
        context.finalized = True
        c.store.save_context(context)
        return {"context": context.model_dump(mode="json")}

    @app.post("/api/journeys/{session_id}/rating")
    async def feedback(
        session_id: str,
        payload: FeedbackRequest,
        request: Request,
        background: BackgroundTasks,
    ) -> dict[str, Any]:
        c = container(request)
        session = require_session(c, request, session_id)
        plan = c.store.get_plan(session_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="No plan for this session")
        context = c.store.get_context(session_id)
        if context is None or not context.finalized:
            raise HTTPException(status_code=409, detail="Finalise the plan before rating it.")
        token = (payload.client_token or session.client_token).strip()
        if token and payload.useful:
            # Derived preferences go to the caller's own profile only.
            token = resolve_identity(request, c, token)
        record = Feedback(
            trip_id=plan.trip_id,
            useful=payload.useful,
            stars=payload.stars,
            reason_code=payload.reason_code,
        )
        c.store.add_feedback(record)
        added: list[str] = []
        if payload.useful:
            background.add_task(
                c.memory.save_useful_trip,
                plan,
                stars=payload.stars,
                owner=session.owner or None,
            )
            if token:
                derived = derive_preferences(plan, context)
                existing = c.store.get_preferences(token)
                merged: list[str] = list(existing.preferences) if existing else []
                for value in map(fit_preference, derived):
                    if value not in merged:
                        merged.append(value)
                        added.append(value)
                c.store.upsert_preferences(PreferenceProfile(client_token=token, preferences=merged))
        return {
            "feedback": record.model_dump(mode="json"),
            "preferences_added": added,
        }

    @app.post("/api/journeys/{session_id}/share")
    async def share_session(session_id: str, request: Request) -> dict[str, Any]:
        c = container(request)
        require_session(c, request, session_id)
        plan = c.store.get_plan(session_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="No plan for this session")
        await charge_create(request)
        slug, expires_at = c.store.create_share(session_id, plan)
        return {"slug": slug, "url": f"/s/{slug}", "expires_at": expires_at}

    @app.delete("/api/journeys/{session_id}/share")
    async def revoke_share(session_id: str, request: Request) -> dict[str, Any]:
        """Take every public link of this session down at once."""
        c = container(request)
        require_session(c, request, session_id)
        return {"revoked": c.store.revoke_shares(session_id)}

    # An expired or revoked link answers exactly like one that never existed.
    @app.get("/api/public/{slug}")
    async def shared_json(slug: str, request: Request) -> dict[str, Any]:
        data = container(request).store.get_share(slug)
        if data is None:
            raise HTTPException(status_code=404, detail="Shared trip not found")
        return data

    @app.get("/s/{slug}", response_class=HTMLResponse)
    async def shared_page(slug: str, request: Request) -> HTMLResponse:
        data = container(request).store.get_share(slug)
        if data is None:
            return HTMLResponse("<h1>Trip not found</h1>", status_code=404)
        days = "".join(
            "<section><h2>Day " + str(index + 1) + " · " + html.escape(day.get("date", "")) + "</h2>"
            + "".join(
                "<p><strong>" + html.escape(item.get("start", "")) + "–" + html.escape(item.get("end", ""))
                + " " + html.escape(item.get("name", "")) + "</strong><br>"
                + html.escape(item.get("notes", "")) + "</p>"
                for item in day.get("items", [])
            )
            + "</section>"
            for index, day in enumerate(data.get("days", []))
        )
        destination = html.escape(data.get("destination", {}).get("name", "Trip"))
        dates = data.get("dates", {})
        body = f"""<!doctype html><html><head><meta charset="utf-8"><title>{destination} · Pathfinder</title>
<style>body{{max-width:760px;margin:40px auto;padding:0 20px;background:#f4efe5;color:#211d15;font-family:system-ui,sans-serif}}
main{{background:#fff;border:1px solid #ddd5c7;border-radius:20px;padding:28px}}h1{{font-size:30px}}
section{{border-top:1px solid #eee7db;padding-top:14px;margin-top:14px}}p{{font-size:14px;line-height:1.5}}
small{{color:#8a8173}}</style></head><body><main><small>SHARED PATHFINDER TRIP · READ ONLY</small>
<h1>{destination}</h1><p>{html.escape(str(dates.get("start", "")))} to {html.escape(str(dates.get("end", "")))}</p>
<p>{html.escape(data.get("summary", ""))}</p>{days}<p><small>No account, budget, party size or session id is exposed.</small></p>
</main></body></html>"""
        return HTMLResponse(body)

    @app.delete("/api/journeys/{session_id}")
    async def discard_session(session_id: str, request: Request) -> dict[str, Any]:
        c = container(request)
        require_session(c, request, session_id)
        return {"deleted": c.store.delete_session(session_id), "session_id": session_id}

    @app.delete("/api/journeys/{session_id}/turns")
    async def clear_messages(session_id: str, request: Request) -> dict[str, Any]:
        c = container(request)
        require_session(c, request, session_id)
        deleted = c.store.delete_messages(session_id)
        return {"deleted": deleted, "session_id": session_id}

    @app.get("/api/traveller")
    async def get_profile(
        request: Request,
        client_token: str = Query(default="", max_length=TOKEN_MAX_LENGTH),
    ) -> dict[str, Any]:
        c = container(request)
        return c.store.client_profile(resolve_identity(request, c, client_token))

    @app.patch("/api/traveller")
    async def patch_profile(payload: ProfilePatch, request: Request) -> dict[str, Any]:
        c = container(request)
        identity = resolve_identity(request, c, payload.client_token)
        profile = PreferenceProfile(
            client_token=identity,
            preferences=[value.strip() for value in payload.preferences if value.strip()],
        )
        c.store.upsert_preferences(profile)
        return {"profile": c.store.client_profile(identity)}

    register_llm_key_routes(
        app,
        settings=settings,
        container=container,
        resolve_identity=resolve_identity,
        require_admin=require_admin,
        charge_key_test=charge_key_test,
    )
    register_serpapi_routes(app, container=container, require_admin=require_admin)

    @app.post("/api/recall/clear")
    async def clear_memory(request: Request, payload: ClearMemoryRequest | None = None) -> dict[str, Any]:
        c = container(request)
        identity = resolve_identity(request, c, payload.client_token if payload else "")
        cleared = c.store.clear_memory(identity)
        return {"cleared": True, **cleared}

    @app.get("/api/wishlist/{saved_id}")
    async def saved_trip(request: Request, saved_id: str, client_token: str = Query(default="", max_length=TOKEN_MAX_LENGTH)) -> dict[str, Any]:
        c = container(request)
        trip = c.store.saved_trip_for_owner(resolve_identity(request, c, client_token), saved_id)
        if trip is None:
            raise HTTPException(status_code=404, detail="That saved trip was not found.")
        days = []
        if trip.plan is not None:
            for day in trip.plan.days:
                days.append({
                    "date": day.date.isoformat() if hasattr(day.date, "isoformat") else str(day.date),
                    "title": day.title,
                    "items": [item.name for item in day.items],
                })
        return {
            "id": trip.id,
            "destination": trip.destination,
            "summary": trip.summary,
            "stars": trip.stars,
            "days": days,
        }

    @app.get("/api/wishlist")
    async def saved_trips(
        request: Request,
        limit: int = Query(default=20, ge=1, le=100),
        client_token: str = Query(default="", max_length=TOKEN_MAX_LENGTH),
    ) -> dict[str, Any]:
        c = container(request)
        trips = c.store.list_saved_trips_for_owner(resolve_identity(request, c, client_token), limit)
        return {
            "trips": [
                {
                    "id": trip.id,
                    "trip_id": trip.trip_id,
                    "destination": trip.destination,
                    "rating": trip.rating,
                    "stars": trip.stars,
                    "summary": trip.summary,
                    "created_at": trip.created_at.isoformat(),
                    "days": len(trip.plan.days) if trip.plan else None,
                }
                for trip in trips
            ]
        }

    @app.get("/api/popular")
    async def trending(
        request: Request,
        q: str = Query(default="", max_length=80),
        limit: int = Query(default=6, ge=1, le=12),
        client_token: str = Query(default="", max_length=TOKEN_MAX_LENGTH),
        destination: str = Query(default="", max_length=80),
    ) -> dict[str, Any]:
        """Hybrid retrieval over useful-marked trips. Public fields only.

        Public without an identity. Personal matching uses the caller's own
        preferences only: a `user:` token needs that user's bearer (401).
        """
        c = container(request)
        preferences: list[str] = []
        token = presented_token(request, client_token)
        user = checked_user(request, c, token)
        identity = f"{USER_PREFIX}{user.id}" if user is not None else token
        if identity:
            profile = c.store.get_preferences(identity)
            if profile is not None:
                preferences = list(profile.preferences)
        return trending_report(
            c.store,
            q,
            limit,
            preferences=preferences,
            destination=destination,
        )

    # -- chat -------------------------------------------------------------

    @app.post("/api/turn/stream")
    async def chat_stream(payload: ChatRequest, request: Request) -> StreamingResponse:
        c = container(request)
        attachments = _attachments(payload.attachments, settings)
        session_id = payload.session_id
        if not session_id:
            token, user = session_caller(request, c, payload.client_token)
            if not token and user is None:
                # A trip nobody could open again would still cost a whole planning turn.
                raise HTTPException(status_code=401, detail="Sign in or send a client token.")
            await charge_ai(request)
            session = c.store.create_session(token, user.id if user else "")
            session_id = session.id
            c.store.save_context(
                TripContext(
                    session_id=session_id,
                    language=payload.locale,
                    party_size=None,
                    currency=settings.default_currency,
                )
            )
        else:
            require_session(c, request, session_id)
            await charge_ai(request)

        async def event_stream() -> AsyncIterator[str]:
            # A message waits for the earlier ones on its trip; the waiting does not count against its deadline.
            deadline = Deadline(settings.turn_deadline_seconds)
            turn = c.turn_service.handle_turn(
                session_id=session_id,
                message=payload.message,
                locale=payload.locale,
                scope_item_id=payload.scope_item_id,
                scope_label=payload.scope_label,
                developer=payload.developer,
                review=payload.review,
                attachments=attachments,
                on_wait=deadline.pause,
                on_start=deadline.restart,
                on_segments=lambda count: deadline.allow(
                    turn_deadline_for(settings, count) - settings.turn_deadline_seconds
                ),
            )

            def owed_key_notice() -> list[str]:
                # A caller key that failed before the turn listened for live events (understand, the router, an
                # ask turn) was never told: tell it once, before the last frame. The stream runs inside the
                # request's caller binding (a pure ASGI middleware), and the turn's tasks share that state.
                notice = take_key_notice()
                return [sse_frame("llm_key", notice)] if notice else []

            try:
                async with aclosing(
                    paced(
                        turn,
                        keepalive_seconds=settings.sse_keepalive_seconds,
                        deadline_seconds=deadline,
                    )
                ) as events:
                    async for event in events:
                        if event is KEEPALIVE:
                            yield KEEPALIVE_FRAME
                        elif event is TIMED_OUT:
                            for frame in owed_key_notice():
                                yield frame
                            # The turn task is already cancelled; this is the last frame.
                            yield sse_frame(
                                "error",
                                {
                                    "code": "turn_timeout",
                                    "message": "This took too long and was stopped. Please try again.",
                                    "detail": f"No result within {settings.turn_deadline_seconds:g} seconds.",
                                },
                            )
                            return
                        else:
                            if event.event in {"done", "error"}:
                                for frame in owed_key_notice():
                                    yield frame
                            yield sse_frame(event.event, event.data)
                for frame in owed_key_notice():
                    yield frame
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                for frame in owed_key_notice():
                    yield frame
                yield sse_frame(
                    "error",
                    {"code": "stream_error", "message": "The stream ended unexpectedly.", "detail": user_facing_error(exc)},
                )

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # -- evaluation -------------------------------------------------------

    @app.get("/api/eval/suites/{dataset_id}/cases", dependencies=[Depends(require_admin)])
    async def eval_suite_cases(dataset_id: str) -> dict[str, Any]:
        from app.evaluation import suite_preview

        try:
            cases = suite_preview(dataset_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Dataset not found")
        return {"cases": cases}

    @app.get("/api/eval/suites", dependencies=[Depends(require_admin)])
    async def eval_datasets(request: Request) -> dict[str, Any]:
        c = container(request)
        return {"datasets": [dataset.model_dump(mode="json") for dataset in c.store.list_eval_datasets()]}

    @app.post("/api/eval/suites/{dataset_id}/lock", dependencies=[Depends(require_admin)])
    async def freeze_dataset(dataset_id: str, request: Request) -> dict[str, Any]:
        c = container(request)
        try:
            meta = c.store.freeze_eval_dataset(dataset_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="Dataset not found")
        return {"dataset": meta.model_dump(mode="json")}

    @app.get("/api/eval/trials", dependencies=[Depends(require_admin)])
    async def eval_runs(request: Request, limit: int = 30) -> dict[str, Any]:
        c = container(request)
        runs = c.store.list_eval_runs(limit)
        return {
            "runs": [
                {
                    "id": run.id,
                    "experiment_tag": run.experiment_tag,
                    "dataset_id": run.dataset_id,
                    "dataset_version": run.dataset_version,
                    "status": run.status,
                    "metrics": [metric.model_dump() for metric in run.metrics],
                    "latency_ms": run.latency_ms,
                    "token_usage": run.token_usage,
                    "created_at": run.created_at.isoformat(),
                    "case_count": len(run.cases),
                    "passed": sum(1 for case in run.cases if case.passed),
                }
                for run in runs
            ]
        }

    # An evaluation run plans every case in a dataset (and may call the judge model).
    @app.post("/api/eval/trials", dependencies=[Depends(require_admin), limit_ai])
    async def run_eval(payload: EvalRunRequest, request: Request) -> dict[str, Any]:
        c = container(request)
        try:
            run = await c.evaluation.run(
                dataset_id=payload.dataset_id,
                experiment_tag=payload.experiment_tag,
                split=payload.split,
            )
        except KeyError:
            raise HTTPException(status_code=404, detail="Dataset not found")
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return {"run": run.model_dump(mode="json")}

    @app.get("/api/eval/trials/{run_id}", dependencies=[Depends(require_admin)])
    async def eval_run(run_id: str, request: Request) -> dict[str, Any]:
        c = container(request)
        run = c.store.get_eval_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return {"run": run.model_dump(mode="json")}

    @app.get("/api/eval/trials/{run_id}/export", response_class=PlainTextResponse, dependencies=[Depends(require_admin)])
    async def eval_export(run_id: str, request: Request) -> str:
        c = container(request)
        run = c.store.get_eval_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return export_markdown(run)

    @app.get("/api/eval/contrast", dependencies=[Depends(require_admin)])
    async def eval_compare(a: str, b: str, request: Request) -> dict[str, Any]:
        c = container(request)
        run_a = c.store.get_eval_run(a)
        run_b = c.store.get_eval_run(b)
        if run_a is None or run_b is None:
            raise HTTPException(status_code=404, detail="Run not found")
        metrics_a = {metric.key: metric.value for metric in run_a.metrics}
        metrics_b = {metric.key: metric.value for metric in run_b.metrics}
        keys = sorted(set(metrics_a) | set(metrics_b))
        return {
            "a": {"id": run_a.id, "tag": run_a.experiment_tag},
            "b": {"id": run_b.id, "tag": run_b.experiment_tag},
            "rows": [
                {
                    "key": key,
                    "a": metrics_a.get(key),
                    "b": metrics_b.get(key),
                    "delta": round(metrics_b.get(key, 0) - metrics_a.get(key, 0), 2),
                }
                for key in keys
            ],
        }

    @app.get("/api/admin/desk", dependencies=[Depends(require_admin)])
    async def desk(request: Request) -> dict[str, Any]:
        store = container(request).store
        seen = store.seen_map()
        now = datetime.now(timezone.utc)
        cards = []
        policy = store.escalation_policy()
        for session in store.recent_sessions(80):
            messages = store.list_messages(session["id"], limit=40)
            last = messages[-1] if messages else None
            state, on_page = _desk_liveness(session["updated_at"], seen.get(session["id"]), now)
            parts = policy.get("parts") if isinstance(policy.get("parts"), dict) else {}
            stuck_part = parts.get("stuck") if isinstance(parts.get("stuck"), dict) else {}
            stuck = bool(stuck_part.get("enabled")) and is_stuck(
                messages,
                int(stuck_part.get("after") or 3),
            )
            flagged = agent_reason(0, stuck)
            if flagged and not store.is_held(session["id"]) and not store.returned_chat(session["id"]):
                store.hold_chat(session["id"], "")
            cards.append(
                {
                    "session_id": session["id"],
                    "created_at": session.get("created_at") or session["updated_at"],
                    "updated_at": session["updated_at"],
                    "state": state,
                    "on_page": on_page,
                    "held": store.is_held(session["id"]),
                    "agent_name": store.held_agent(session["id"]),
                    "stuck": stuck,
                    "frustration": 0,
                    "frustration_kind": "none",
                    "band": "calm",
                    "sentiment_score": 0.0,
                    "sentiment_label": "",
                    "sentiment_notes": [],
                    "preview": (last.content if last else "")[:160],
                    "last_role": last.role if last else "",
                    **_desk_contact(store, session["id"]),
                }
            )
        cards.sort(key=lambda card: (card["frustration"], card["updated_at"]), reverse=True)
        return {"chats": cards}

    @app.get("/api/admin/desk/{session_id}", dependencies=[Depends(require_admin)])
    async def desk_chat(session_id: str, request: Request) -> dict[str, Any]:
        store = container(request).store
        messages = store.list_messages(session_id, limit=80)
        return {
            "session_id": session_id,
            "held": store.is_held(session_id),
            "note": store.desk_note(session_id),
            "contact": _desk_contact(store, session_id),
            "messages": [
                {
                    "id": message.id,
                    "role": message.role,
                    "content": message.content,
                    "at": message.created_at.isoformat(),
                    "frustration": 0,
                    "sentiment_label": "",
                    "live_agent": bool((message.meta or {}).get("live_agent") or (message.meta or {}).get("operator")),
                    "operator": bool((message.meta or {}).get("operator")),
                }
                for message in messages
            ],
        }

    def operator_name(request: Request) -> str:
        """The signed-in account's name. A desk with no account is still a live agent."""
        user = current_user(request, container(request))
        if user is not None:
            named = user.display_name.strip() or user.email.split("@")[0]
            if named:
                return named[:80]
        return "Live agent"

    @app.post("/api/admin/desk/{session_id}/hold", dependencies=[Depends(require_admin)])
    async def desk_hold(session_id: str, request: Request) -> dict[str, Any]:
        name = operator_name(request)
        container(request).store.hold_chat(session_id, name)
        return {"held": True, "agent_name": name}

    @app.post("/api/admin/desk/{session_id}/release", dependencies=[Depends(require_admin)])
    async def desk_release(session_id: str, request: Request) -> dict[str, bool]:
        container(request).store.release_chat(session_id)
        return {"held": False}

    @app.post("/api/admin/desk/{session_id}/note", dependencies=[Depends(require_admin)])
    async def desk_note(session_id: str, payload: DeskNoteIn, request: Request) -> dict[str, str]:
        container(request).store.set_desk_note(session_id, payload.note)
        return {"note": payload.note.strip()}

    @app.post("/api/admin/desk/{session_id}/say", dependencies=[Depends(require_admin)])
    async def desk_say(session_id: str, payload: DeskSay, request: Request) -> dict[str, str]:
        message = Message(
            session_id=session_id,
            role="assistant",
            content=payload.text.strip(),
            intent=Intent.ASK,
            meta={"live_agent": True, "agent_name": operator_name(request)},
        )
        container(request).store.add_message(message)
        return {"id": message.id}

    @app.post("/api/admin/desk/{session_id}/run", dependencies=[Depends(require_admin)])
    async def desk_run(session_id: str, payload: DeskRunIn, request: Request) -> dict[str, Any]:
        """Run the plan, change, or question path for a chat a person has taken over."""
        c = container(request)
        if c.store.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="Unknown session")
        if not c.store.is_held(session_id):
            raise HTTPException(status_code=409, detail="Take this chat over first.")
        reply: list[str] = []
        intent = ""
        error = ""
        async for event in c.turn_service.handle_turn(
            session_id=session_id,
            message=payload.message.strip(),
            operator=True,
            operator_name=operator_name(request),
        ):
            if event.event == "token":
                reply.append(str((event.data or {}).get("text") or ""))
            elif event.event == "done":
                intent = str((event.data or {}).get("intent") or "")
            elif event.event == "error":
                error = str((event.data or {}).get("message") or "The workflow stopped.")
        if error and not reply:
            raise HTTPException(status_code=502, detail=error)
        return {"reply": "".join(reply), "intent": intent}

    @app.post("/api/admin/desk/{session_id}/brief", dependencies=[Depends(require_admin)])
    async def desk_brief(session_id: str, payload: DeskBriefIn, request: Request) -> dict[str, Any]:
        """Save the trip form. The desk does not start a plan from these fields."""
        c = container(request)
        if c.store.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="Unknown session")
        if not c.store.is_held(session_id):
            raise HTTPException(status_code=409, detail="Take this chat over first.")
        if payload.end_date < payload.start_date:
            raise HTTPException(status_code=422, detail="The end date is before the start date.")
        context = c.store.get_context(session_id) or TripContext(session_id=session_id)
        context.destination = payload.destination.strip()
        context.origin = payload.origin.strip()
        context.start_date = payload.start_date
        context.end_date = payload.end_date
        context.days = (payload.end_date - payload.start_date).days + 1
        context.party_size = payload.party_size
        context.budget = payload.budget
        context.currency = payload.currency.strip().upper() or "HKD"
        context.preferences = [item.strip() for item in payload.preferences if item.strip()][:20]
        c.store.save_context(context)
        return {"built": False, "plan": _desk_plan_view(c.store.get_plan(session_id))}

    @app.get("/api/admin/desk/{session_id}/plan", dependencies=[Depends(require_admin)])
    async def desk_plan(session_id: str, request: Request) -> dict[str, Any]:
        plan = container(request).store.get_plan(session_id)
        return {"plan": _desk_plan_view(plan)}

    @app.post("/api/admin/desk/{session_id}/plan", dependencies=[Depends(require_admin)])
    async def desk_plan_edit(session_id: str, payload: DeskPlanIn, request: Request) -> dict[str, Any]:
        """Rename, retime, or remove stops. Writes a new plan version."""
        from datetime import datetime, timezone

        c = container(request)
        if not c.store.is_held(session_id):
            raise HTTPException(status_code=409, detail="Take this chat over first.")
        if c.turn_service.session_busy(session_id):
            raise HTTPException(status_code=409, detail=c.turn_service.session_activity(session_id) or "Pathfinder is changing this plan.")
        plan = c.store.get_plan(session_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="This trip has no plan.")
        if not payload.items:
            raise HTTPException(status_code=400, detail="Nothing to save")
        if payload.version != plan.version:
            raise HTTPException(status_code=409, detail="This edit was made on an earlier version. Open it again.")
        try:
            updated = _apply_desk_edits(plan, payload.items)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        updated.version = plan.version + 1
        updated.change_summary = "A person edited the plan."
        updated.updated_at = datetime.now(timezone.utc)
        c.store.save_plan(updated, updated.change_summary)
        name = operator_name(request)
        c.store.add_message(
            Message(
                session_id=session_id,
                role="assistant",
                content="I've updated the plan by hand. Have a look — it's on the plan now.",
                intent=Intent.MODIFY,
                meta={"live_agent": True, "agent_name": name, "desk_plan": True},
            )
        )
        return {"plan": _desk_plan_view(updated)}

    @app.post("/api/admin/desk/{session_id}/edit", dependencies=[Depends(require_admin)])
    async def desk_edit(session_id: str, payload: DeskEditIn, request: Request) -> dict[str, Any]:
        """Ask the edit-itinerary skill to change the plan. A selected stop is what "this" means."""
        from app.domain.errors import PlanInvalidError

        c = container(request)
        if c.store.get_session(session_id) is None:
            raise HTTPException(status_code=404, detail="Unknown session")
        if not c.store.is_held(session_id):
            raise HTTPException(status_code=409, detail="Take this chat over first.")
        if c.store.get_plan(session_id) is None:
            raise HTTPException(status_code=404, detail="This trip has no plan.")
        try:
            reply = await c.turn_service.edit_from_desk(
                session_id,
                payload.instruction.strip(),
                agent_name=operator_name(request),
                scope_item_id=payload.item_id.strip() or None,
            )
        except PlanInvalidError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("desk edit failed")
            raise HTTPException(status_code=502, detail="The edit could not be applied.") from exc
        return {"reply": reply, "plan": _desk_plan_view(c.store.get_plan(session_id))}

    @app.get("/api/admin/desk/{session_id}/itinerary", dependencies=[Depends(require_admin)])
    async def desk_itinerary(session_id: str, request: Request) -> dict[str, Any]:
        """The plan the writer saved, the same object the traveller's page shows."""
        c = container(request)
        plan = c.store.get_plan(session_id)
        if plan is None:
            return {
                "plan": None,
                "planner_busy": c.turn_service.session_busy(session_id),
                "planner_note": c.turn_service.session_activity(session_id),
            }
        return {
            "plan": tidy_data_gaps(plan, drop_edit_gaps=True).model_dump(mode="json"),
            "planner_busy": c.turn_service.session_busy(session_id),
            "planner_note": c.turn_service.session_activity(session_id),
        }

    @app.get("/api/admin/desk/{session_id}/revisions", dependencies=[Depends(require_admin)])
    async def desk_revisions(session_id: str, request: Request) -> dict[str, Any]:
        return {"versions": container(request).store.list_versions(session_id)}

    @app.post("/api/admin/desk/{session_id}/undo", dependencies=[Depends(require_admin)])
    async def desk_undo(session_id: str, payload: RestoreRequest, request: Request) -> dict[str, Any]:
        """Copy an earlier version forward, so the latest plan is that one again."""
        from datetime import datetime, timezone

        c = container(request)
        if not c.store.is_held(session_id):
            raise HTTPException(status_code=409, detail="Take this chat over first.")
        if c.turn_service.session_busy(session_id):
            raise HTTPException(status_code=409, detail=c.turn_service.session_activity(session_id) or "Pathfinder is changing this plan.")
        current = c.store.get_plan(session_id)
        chosen = c.store.get_plan(session_id, payload.version)
        if current is None or chosen is None:
            raise HTTPException(status_code=404, detail="That version is not on this trip.")
        if chosen.version == current.version:
            shown = tidy_data_gaps(current, drop_edit_gaps=True)
            return {"plan": shown.model_dump(mode="json")}
        restored = chosen.model_copy(deep=True)
        restored.version = current.version + 1
        restored.change_summary = f"Undid to version {chosen.version}"
        restored.updated_at = datetime.now(timezone.utc)
        try:
            c.store.save_plan(restored, restored.change_summary)
        except PlanConflict as exc:
            raise HTTPException(status_code=409, detail="The plan changed meanwhile; reload it and try again.") from exc
        c.store.add_message(
            Message(
                session_id=session_id,
                role="assistant",
                content="I've put the previous plan back.",
                intent=Intent.MODIFY,
                meta={"live_agent": True, "agent_name": operator_name(request), "desk_plan": True},
            )
        )
        shown = tidy_data_gaps(c.store.get_plan(session_id) or restored, drop_edit_gaps=True)
        return {"plan": shown.model_dump(mode="json")}

    @app.post("/api/admin/desk/{session_id}/layout", dependencies=[Depends(require_admin)])
    async def desk_layout(session_id: str, payload: LayoutRequest, request: Request) -> dict[str, Any]:
        """Write the same direct-edit grid the writer uses onto this plan."""
        from app.application.proposals import apply_layout

        c = container(request)
        if not c.store.is_held(session_id):
            raise HTTPException(status_code=409, detail="Take this chat over first.")
        if c.turn_service.session_busy(session_id):
            raise HTTPException(status_code=409, detail=c.turn_service.session_activity(session_id) or "Pathfinder is changing this plan.")
        plan = c.store.get_plan(session_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="This trip has no plan.")
        if payload.plan_version is not None and payload.plan_version != plan.version:
            raise HTTPException(
                status_code=409,
                detail="This edit was made on an earlier version of the plan; open the latest version and edit again.",
            )
        try:
            updated = apply_layout(plan, [item.model_dump() for item in payload.items], payload.drops)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc) or "That edit does not fit this plan.") from None
        if updated is not plan:
            context = c.store.get_context(session_id)
            toolbox = Toolbox(path="modify", tracer=NoopTracer(), store=c.store, request_id=new_id("layout"))
            await refresh_transfers(
                plan,
                updated,
                toolbox=toolbox,
                source=getattr(c.registry, "transfers", None),
                buffer=PACE_BUFFER_MINUTES.get(_pace(context) if context is not None else "balanced", 5),
            )
            said = await sync_session_transfers(
                updated, registry=c.registry, store=c.store, session_id=session_id, before=plan
            )
            from app.application.airport_transfers import dropped_hand_edit

            lost = dropped_hand_edit(plan, updated, allowed=set(payload.drops))
            if lost:
                raise HTTPException(status_code=422, detail=lost)
            if said:
                updated.change_summary = " ".join([updated.change_summary, *said]).strip()
            try:
                c.store.save_plan(updated, updated.change_summary)
            except PlanConflict as exc:
                raise HTTPException(
                    status_code=409, detail="The plan changed meanwhile; reload it and edit again."
                ) from exc
            c.store.add_message(
                Message(
                    session_id=session_id,
                    role="assistant",
                    content="I've updated the plan. Have a look — it's the same plan.",
                    intent=Intent.MODIFY,
                    meta={"live_agent": True, "agent_name": operator_name(request), "desk_plan": True},
                )
            )
        shown = tidy_data_gaps(c.store.get_plan(session_id) or updated, drop_edit_gaps=True)
        return {"plan": shown.model_dump(mode="json")}

    def _prompt_allowed(name: str) -> bool:
        if name in _PROMPT_NAMES:
            return True
        return any(prompt == name for prompt, _, _ in prompt_entries())

    @app.put("/api/admin/runtime", dependencies=[Depends(require_admin)])
    async def runtime_put(payload: RuntimeIn, request: Request) -> dict[str, Any]:
        values: dict[str, str] = {}
        if payload.thresholds is not None:
            missing = [key for key in ("router_confidence", "memory_similarity", "preplan_budget_seconds", "max_repairs") if key not in payload.thresholds]
            if missing:
                raise HTTPException(status_code=400, detail="Missing " + ", ".join(missing))
            values.update(
                {
                    "router_confidence": str(payload.thresholds["router_confidence"]),
                    "memory_similarity": str(payload.thresholds["memory_similarity"]),
                    "preplan_budget_seconds": str(payload.thresholds["preplan_budget_seconds"]),
                    "max_repairs": str(payload.thresholds["max_repairs"]),
                }
            )
            if "conversation_turns" in payload.thresholds:
                values["conversation_turns"] = str(payload.thresholds["conversation_turns"])
            if "rule_passes" in payload.thresholds:
                values["rule_passes"] = str(payload.thresholds["rule_passes"])
            for key in ("reviewer_timeout_seconds", "turn_deadline_seconds", "llm_timeout_seconds"):
                if key in payload.thresholds:
                    values[key] = str(payload.thresholds[key])
        if payload.freshness_hours is not None:
            missing = [key for key in ("weather", "tickets", "hotels", "attractions") if key not in payload.freshness_hours]
            if missing:
                raise HTTPException(status_code=400, detail="Missing " + ", ".join(missing))
            values.update(
                {
                    "weather": str(payload.freshness_hours["weather"]),
                    "tickets": str(payload.freshness_hours["tickets"]),
                    "hotels": str(payload.freshness_hours["hotels"]),
                    "attractions": str(payload.freshness_hours["attractions"]),
                }
            )
        if payload.gate is not None:
            values["router_gate"] = payload.gate
        drop: list[str] = []
        secrets_save: dict[str, str] = {}
        secrets_clear: list[str] = []
        if payload.plain_model is not None:
            _take_token(payload.plain_model, "gate_plain_model", values, drop)
        if payload.reason_model is not None:
            _take_token(payload.reason_model, "gate_reason_model", values, drop)
        if payload.writer_model is not None:
            _take_token(payload.writer_model, "writer_model", values, drop)
        _take_side(payload.jev, "jev", "decision_model", "decision_api_url", values, drop, secrets_save, secrets_clear)
        _take_side(payload.fast, "fast", "gate_fast_model", "gate_fast_base_url", values, drop, secrets_save, secrets_clear)
        if payload.poe is not None:
            if payload.poe.clear_key:
                secrets_clear.append("poe")
            elif payload.poe.api_key.strip():
                secrets_save["poe"] = payload.poe.api_key.strip()
        _take_arm(payload.plain, "plain", values, drop, secrets_save, secrets_clear)
        _take_arm(payload.reason, "reason", values, drop, secrets_save, secrets_clear)
        _take_reviewer(payload.reviewer, values, drop, secrets_save, secrets_clear)
        _take_writer_call(payload.writer_call, values)
        if payload.redaction is not None:
            from app.security.pii import check_pattern

            chosen = payload.redaction
            try:
                patterns = {
                    "redact_hkid_pattern": check_pattern(chosen.hkid_pattern),
                    "redact_hk_phone_pattern": check_pattern(chosen.hk_phone_pattern),
                    "redact_email_pattern": check_pattern(chosen.email_pattern),
                    "redact_card_pattern": check_pattern(chosen.card_pattern),
                }
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            values.update(
                {
                    "redact_hkid": "1" if chosen.hkid else "0",
                    "redact_hk_phone": "1" if chosen.hk_phone else "0",
                    "redact_email": "1" if chosen.email else "0",
                    "redact_card": "1" if chosen.card else "0",
                    **patterns,
                }
            )
        if not values and not drop and not secrets_save and not secrets_clear:
            raise HTTPException(status_code=400, detail="Nothing to save")
        store = container(request).store
        box = container(request).box
        try:
            store.check_runtime(settings, values)
            for plain in secrets_save.values():
                _check_secret(plain)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if drop:
            store.delete_runtime_keys(drop)
        if values:
            store.save_runtime(values)
        for name in secrets_clear:
            store.clear_gate_secret(name)
        for name, plain in secrets_save.items():
            store.save_gate_secret(name, box.encrypt(plain))
        store.apply_runtime(settings)
        store.apply_gate_secrets(settings, box)
        return _runtime_view(settings, store)

    @app.post("/api/admin/redaction/preview", dependencies=[Depends(require_admin)])
    async def redaction_preview(payload: RedactionPreviewIn) -> dict[str, str]:
        from app.security.pii import Redaction, check_pattern, preview_redaction

        try:
            chosen = Redaction(
                hkid=payload.hkid,
                hk_phone=payload.hk_phone,
                email=payload.email,
                card=payload.card,
                hkid_pattern=check_pattern(payload.hkid_pattern),
                hk_phone_pattern=check_pattern(payload.hk_phone_pattern),
                email_pattern=check_pattern(payload.email_pattern),
                card_pattern=check_pattern(payload.card_pattern),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return preview_redaction(payload.text, chosen)

    @app.delete("/api/admin/runtime", dependencies=[Depends(require_admin)])
    async def runtime_delete(request: Request) -> dict[str, Any]:
        store = container(request).store
        store.clear_runtime()
        store.apply_runtime(settings)
        store.apply_gate_secrets(settings, container(request).box)
        return _runtime_view(settings, store)

    @app.get("/api/admin/escalation", dependencies=[Depends(require_admin)])
    async def escalation_get(request: Request) -> dict[str, Any]:
        c = container(request)
        return _escalation_view(c.store.escalation_policy(), settings, c.store.gate_secret_saved("handoff"))

    @app.put("/api/admin/escalation", dependencies=[Depends(require_admin)])
    async def escalation_put(payload: EscalationIn, request: Request) -> dict[str, Any]:
        c = container(request)
        if payload.clear_key:
            c.store.clear_gate_secret("handoff")
        elif payload.api_key.strip():
            try:
                _check_secret(payload.api_key.strip())
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            c.store.save_gate_secret("handoff", c.box.encrypt(payload.api_key.strip()))
        c.store.apply_gate_secrets(settings, c.box)
        saved = c.store.save_escalation_policy(
            enabled=payload.enabled,
            message=payload.message,
            frustration_at=payload.frustration_at,
            examples=payload.examples,
            match_mode=payload.match_mode,
            use_jev=payload.use_jev,
            stuck_on=payload.stuck_on,
            stuck_after=payload.stuck_after,
            words=payload.words,
            parts=payload.monitors.model_dump() if payload.monitors else None,
        )
        return _escalation_view(saved, settings, c.store.gate_secret_saved("handoff"))

    @app.get("/api/admin/cases", dependencies=[Depends(require_admin)])
    async def review_cases(request: Request) -> dict[str, Any]:
        from app.application.case_review import failed_cases

        c = container(request)
        return {"cases": failed_cases(c.store), "suggestions": c.store.list_prompt_suggestions()}

    @app.get("/api/admin/cases/turns", dependencies=[Depends(require_admin)])
    async def review_turns(request: Request) -> dict[str, Any]:
        from app.application.case_review import _chat_state

        store = container(request).store
        turns = []
        for row in store.recent_capture_turns(30):
            turns.append(
                {
                    "id": row.get("id") or "",
                    "session_id": row.get("session_id") or "",
                    "session_state": _chat_state(row.get("updated_at"), row.get("seen_at")),
                    "input": (row.get("content") or "")[:500],
                    "reply": (row.get("reply") or "")[:400],
                }
            )
        return {"turns": turns}

    @app.post("/api/admin/cases", dependencies=[Depends(require_admin)])
    async def review_add(payload: OperatorCaseIn, request: Request) -> dict[str, Any]:
        from app.application.case_review import failed_cases

        state = payload.session_state if payload.session_state in {"live", "completed"} else ""
        container(request).store.add_operator_case(
            session_id=payload.session_id.strip(),
            session_state=state,
            message=payload.message,
            expected=payload.expected,
            note=payload.note,
        )
        return {"cases": failed_cases(container(request).store)}

    @app.post("/api/admin/cases/scan", dependencies=[Depends(require_admin), limit_ai])
    async def review_scan(request: Request) -> dict[str, Any]:
        from app.application.case_review import suggest_skill_changes
        from app.domain.errors import ProviderError

        c = container(request)
        try:
            suggestions = await suggest_skill_changes(c.gateway, c.store)
        except ProviderError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"suggestions": suggestions}

    @app.post("/api/admin/cases/check", dependencies=[Depends(require_admin)])
    async def review_check(request: Request) -> dict[str, Any]:
        from app.application.case_review import check_listed

        c = container(request)
        return await check_listed(c.evaluation, c.store)

    @app.post("/api/admin/cases/suggestions/{suggestion_id}/accept", dependencies=[Depends(require_admin)])
    async def review_accept(suggestion_id: str, payload: SuggestionIn, request: Request) -> dict[str, Any]:
        c = container(request)
        current = c.store.get_prompt_suggestion(suggestion_id)
        if current is None or current["status"] != "draft":
            raise HTTPException(status_code=404, detail="Suggestion not found")
        if current.get("kind") == "setting":
            from app.application.case_review import SETTING_RANGES

            key = current["skill"]
            if key not in SETTING_RANGES:
                raise HTTPException(status_code=404, detail="Unknown setting")
            raw = payload.body.strip() or current.get("setting_value") or ""
            try:
                number = float(raw)
            except ValueError:
                raise HTTPException(status_code=400, detail="A number is required")
            low, high = SETTING_RANGES[key]
            if not low <= number <= high:
                raise HTTPException(status_code=400, detail="That number is outside the allowed range")
            value = f"{number:g}"
            try:
                c.store.check_runtime(c.settings, {key: value})
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            c.store.save_runtime({key: value})
            c.store.apply_runtime(c.settings)
            saved = c.store.decide_prompt_suggestion(suggestion_id, "accepted", value)
            return {"suggestion": saved}
        if not _prompt_allowed(current["skill"]):
            raise HTTPException(status_code=404, detail="Unknown prompt")
        body = payload.body.strip() or current["body"]
        c.store.set_prompt_override(current["skill"], body)
        saved = c.store.decide_prompt_suggestion(suggestion_id, "accepted", body)
        return {"suggestion": saved}

    @app.post("/api/admin/cases/suggestions/{suggestion_id}/dismiss", dependencies=[Depends(require_admin)])
    async def review_dismiss(suggestion_id: str, request: Request) -> dict[str, Any]:
        c = container(request)
        current = c.store.get_prompt_suggestion(suggestion_id)
        if current is None or current["status"] not in {"draft", "restored"}:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        saved = c.store.decide_prompt_suggestion(suggestion_id, "dismissed")
        return {"suggestion": saved}

    @app.post("/api/admin/cases/suggestions/{suggestion_id}/restore", dependencies=[Depends(require_admin)])
    async def review_restore(suggestion_id: str, request: Request) -> dict[str, Any]:
        c = container(request)
        current = c.store.get_prompt_suggestion(suggestion_id)
        if current is None:
            raise HTTPException(status_code=404, detail="Suggestion not found")
        if current.get("kind") == "setting":
            c.store.clear_runtime_key(current["skill"])
            c.store.apply_runtime(c.settings)
        else:
            c.store.clear_prompt_override(current["skill"])
        c.store.decide_prompt_suggestion(suggestion_id, "restored")
        return {"ok": True}

    @app.get("/api/admin/prompts", dependencies=[Depends(require_admin)])
    async def prompts_get(request: Request) -> dict[str, Any]:
        store = container(request).store
        rows = [
            {
                "name": "persona",
                "description": "Added to every model that writes to the traveller.",
                "default": PERSONA_RULE,
                "override": store.prompt_override("persona") or "",
            },
            {
                "name": "language",
                "description": "How replies are worded. The translator sets its own language.",
                "default": LANGUAGE_RULE,
                "override": store.prompt_override("language") or "",
            },
        ]
        for name, description, body in prompt_entries():
            rows.append(
                {
                    "name": name,
                    "description": description,
                    "default": body,
                    "override": store.prompt_override(name) or "",
                    "file": prompt_file(name),
                }
            )
        return {"prompts": rows}

    @app.put("/api/admin/prompts/{name}", dependencies=[Depends(require_admin)])
    async def prompts_put(name: str, payload: PromptIn, request: Request) -> dict[str, str]:
        if not _prompt_allowed(name):
            raise HTTPException(status_code=404, detail="Unknown prompt")
        container(request).store.set_prompt_override(name, payload.body)
        return {"name": name}

    @app.delete("/api/admin/prompts/{name}", dependencies=[Depends(require_admin)])
    async def prompts_delete(name: str, request: Request) -> dict[str, bool]:
        if not _prompt_allowed(name):
            raise HTTPException(status_code=404, detail="Unknown prompt")
        container(request).store.clear_prompt_override(name)
        return {"ok": True}

    @app.get("/api/admin/threats", dependencies=[Depends(require_admin)])
    async def injections(request: Request) -> dict[str, Any]:
        store = container(request).store
        return {"events": store.injection_summary(), "hourly": store.injection_hourly()}

    @app.get("/api/admin/spans/{request_id}", dependencies=[Depends(require_admin)])
    async def trace(request_id: str, request: Request) -> dict[str, Any]:
        if not re.fullmatch(r"req_[0-9a-f]{12}", request_id):
            raise HTTPException(status_code=400, detail="Invalid request id")
        path = settings.trace_dir / f"{request_id}.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="Trace not found")
        return json.loads(path.read_text(encoding="utf-8"))

    # -- static frontend --------------------------------------------------

    dist = Path(settings.frontend_dist)
    if dist.exists() and (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", response_class=HTMLResponse)
        async def spa(full_path: str) -> str:
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404)
            index = dist / "index.html"
            return index.read_text(encoding="utf-8")

    return app


def _parse_stamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _desk_liveness(updated_at: str, seen_at: str | None, now: datetime) -> tuple[str, bool]:
    from app.application.desk_sessions import session_state

    updated = _parse_stamp(updated_at) or now
    return session_state(updated, _parse_stamp(seen_at), now)


def _desk_contact(store: Any, session_id: str) -> dict[str, Any]:
    """The trip facts a contact desk shows beside a chat, and who opened the session."""
    context = store.get_context(session_id)
    facts = {
        "destination": "",
        "origin": "",
        "start": "",
        "end": "",
        "party": None,
        "budget": None,
        "currency": "",
    }
    trip_preferences: list[str] = []
    if context is not None:
        facts = {
            "destination": context.destination or "",
            "origin": context.origin or "",
            "start": context.start_date.isoformat() if context.start_date else "",
            "end": context.end_date.isoformat() if context.end_date else "",
            "party": context.party_size,
            "budget": context.budget,
            "currency": context.currency or "",
        }
        trip_preferences = [str(item).strip() for item in (context.preferences or []) if str(item).strip()]
    session = store.get_session(session_id)
    user = store.get_user(session.user_id) if session is not None and session.user_id else None
    identity = f"{USER_PREFIX}{user.id}" if user is not None else (session.client_token if session is not None else "")
    profile = store.get_preferences(identity) if identity else None
    saved = [str(item).strip() for item in (profile.preferences if profile else []) if str(item).strip()]
    preferences: list[str] = []
    for value in saved + trip_preferences:
        if value not in preferences:
            preferences.append(value)
    facts.update(
        {
            "signed_in": user is not None,
            "name": (user.display_name if user is not None else "") or "",
            "email": (user.email if user is not None else "") or "",
            "preferences": preferences[:40],
        }
    )
    return facts


_DESK_TIME = re.compile(r"^\d{2}:\d{2}$")


def _desk_plan_view(plan: Any) -> dict[str, Any] | None:
    if plan is None:
        return None
    return {
        "version": plan.version,
        "days": [
            {
                "date": day.date.isoformat(),
                "title": day.title,
                "items": [
                    {
                        "id": item.id,
                        "name": item.name,
                        "type": item.type.value,
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


def _apply_desk_edits(plan: Any, edits: list[DeskStopIn]) -> Any:
    updated = plan.model_copy(deep=True)
    by_id = {item.id: item for day in updated.days for item in day.items}
    for edit in edits:
        if edit.id not in by_id:
            raise ValueError("That stop is not on this plan.")
        if edit.remove:
            for day in updated.days:
                day.items = [item for item in day.items if item.id != edit.id]
            continue
        name = edit.name.strip()
        if not name:
            raise ValueError("A stop needs a name.")
        if not _DESK_TIME.fullmatch(edit.start) or not _DESK_TIME.fullmatch(edit.end) or edit.end <= edit.start:
            raise ValueError("Times must be HH:MM, and the end after the start.")
        found = by_id[edit.id]
        found.name = name
        found.start = edit.start
        found.end = edit.end
    from pydantic import ValidationError

    try:
        type(updated).model_validate(updated.model_dump())
    except ValidationError as exc:
        raise ValueError("That edit does not fit this plan.") from exc
    return updated


def _take_writer_call(call: WriterCallIn | None, values: dict[str, str]) -> None:
    if call is None:
        return
    if call.temperature is not None:
        values["writer_temperature"] = str(call.temperature)
    if call.max_tokens is not None:
        values["writer_max_tokens"] = str(int(call.max_tokens))
    if call.reasoning.strip():
        values["writer_reasoning"] = call.reasoning.strip().lower()


def _take_reviewer(
    arm: ReviewerIn | None,
    values: dict[str, str],
    drop: list[str],
    secrets_save: dict[str, str],
    secrets_clear: list[str],
) -> None:
    if arm is None:
        return
    _take_token(arm.provider.lower(), "reviewer_provider", values, drop)
    _take_token(arm.model, "reviewer_model", values, drop)
    _take_token(arm.base_url, "reviewer_base_url", values, drop)
    _take_token(arm.reasoning.lower(), "reviewer_reasoning", values, drop)
    if arm.temperature is not None:
        values["reviewer_temperature"] = str(arm.temperature)
    if arm.max_tokens is not None:
        values["reviewer_max_tokens"] = str(int(arm.max_tokens))
    if arm.rounds is not None:
        values["rule_passes"] = str(int(arm.rounds))
    if arm.clear_key:
        secrets_clear.append("reviewer")
    elif arm.api_key.strip():
        secrets_save["reviewer"] = arm.api_key.strip()


def _take_arm(
    arm: GateArmIn | None,
    name: str,
    values: dict[str, str],
    drop: list[str],
    secrets_save: dict[str, str],
    secrets_clear: list[str],
) -> None:
    if arm is None:
        return
    _take_token(arm.provider.lower(), f"gate_{name}_provider", values, drop)
    _take_token(arm.model, f"gate_{name}_model", values, drop)
    _take_token(arm.base_url, f"gate_{name}_base_url", values, drop)
    _take_token(arm.reasoning.lower(), f"gate_{name}_reasoning", values, drop)
    if arm.temperature is not None:
        values[f"gate_{name}_temperature"] = str(arm.temperature)
    if arm.max_tokens is not None:
        values[f"gate_{name}_max_tokens"] = str(int(arm.max_tokens))
    if arm.clear_key:
        secrets_clear.append(name)
    elif arm.api_key.strip():
        secrets_save[name] = arm.api_key.strip()


def _take_token(value: str, key: str, values: dict[str, str], drop: list[str]) -> None:
    text = value.strip()
    if text:
        values[key] = text
    else:
        drop.append(key)


def _take_side(
    side: OrchestratorIn | None,
    secret_name: str,
    model_key: str,
    url_key: str,
    values: dict[str, str],
    drop: list[str],
    secrets_save: dict[str, str],
    secrets_clear: list[str],
) -> None:
    if side is None:
        return
    _take_token(side.model, model_key, values, drop)
    _take_token(side.url, url_key, values, drop)
    if side.clear_key:
        secrets_clear.append(secret_name)
    elif side.api_key.strip():
        secrets_save[secret_name] = side.api_key.strip()


def _check_secret(plain: str) -> None:
    if len(plain) < 8 or len(plain) > 512 or any(character.isspace() for character in plain):
        raise ValueError("The key must be 8 to 512 characters, with no spaces")


def _arm_view(active: Settings, name: str, saved: bool) -> dict[str, Any]:
    """One cloud gate as the form shows it. The key itself stays on the server."""
    plain = name == "plain"
    provider = str(getattr(active, f"gate_{name}_provider", "") or "poe")
    own = str(getattr(active, f"gate_{name}_api_key", "") or "")
    if own:
        shown = _key_view(own, saved)
        source = "arm"
    elif provider == "poe" and active.poe_api_key:
        shown = _key_view(active.poe_api_key, False)
        source = "poe"
    elif provider == "deepseek" and server_deepseek_key(active):
        shown = _key_view(server_deepseek_key(active), False)
        source = "deepseek"
    else:
        shown = _key_view("", False)
        source = "none"
    return {
        "provider": provider,
        "model": str(getattr(active, f"gate_{name}_model", "") or ("gemini-3.5-flash-lite" if plain else "gemini-3.8-flash")),
        "base_url": str(getattr(active, f"gate_{name}_base_url", "") or ""),
        "temperature": float(getattr(active, f"gate_{name}_temperature", 0.0 if plain else 0.2)),
        "max_tokens": int(getattr(active, f"gate_{name}_max_tokens", 1024) or 1024),
        "reasoning": str(getattr(active, f"gate_{name}_reasoning", "") or ("none" if plain else "low")),
        "key_source": source,
        **shown,
    }


def _reviewer_view(active: Settings, saved: bool) -> dict[str, Any]:
    """The reviewer as the form shows it. The key itself stays on the server."""
    provider = str(getattr(active, "reviewer_provider", "") or "poe")
    own = str(getattr(active, "reviewer_api_key", "") or "")
    if own:
        shown = _key_view(own, saved)
        source = "arm"
    elif provider == "poe" and active.poe_api_key:
        shown = _key_view(active.poe_api_key, False)
        source = "poe"
    elif provider == "deepseek" and server_deepseek_key(active):
        shown = _key_view(server_deepseek_key(active), False)
        source = "deepseek"
    else:
        shown = _key_view("", False)
        source = "none"
    return {
        "provider": provider,
        "model": str(getattr(active, "reviewer_model", "") or ""),
        "base_url": str(getattr(active, "reviewer_base_url", "") or ""),
        "temperature": float(getattr(active, "reviewer_temperature", 0.0)),
        "max_tokens": int(getattr(active, "reviewer_max_tokens", 4096) or 4096),
        "reasoning": str(getattr(active, "reviewer_reasoning", "") or "none"),
        "rounds": int(getattr(active, "refine_max_rounds", 3)),
        "key_source": source,
        **shown,
    }


def _escalation_view(policy: dict[str, Any], active: Settings, saved: bool) -> dict[str, Any]:
    """The handoff form. A saved key is this form's. With none, Jev uses the gate key."""
    own = str(getattr(active, "handoff_api_key", "") or "")
    gate = str(active.decision_api_key or "")
    if own:
        shown = _key_view(own, saved)
        source = "saved"
    elif gate:
        shown = _key_view(gate, False)
        source = "gate"
    else:
        shown = _key_view("", False)
        source = "none"
    return {**policy, "key_source": source, **shown}


def _key_view(secret: str, saved: bool) -> dict[str, Any]:
    return {
        "key_set": bool(secret),
        "key_saved": saved,
        "key_tail": secret[-4:] if len(secret) >= 4 else "",
    }


def _redaction_view(active: Settings) -> dict[str, Any]:
    from app.security.pii import DEFAULT_PATTERNS

    def shown(field: str, name: str) -> str:
        saved = str(getattr(active, field, "") or "").strip()
        return saved or DEFAULT_PATTERNS[name]

    return {
        "hkid": bool(getattr(active, "redact_hkid", True)),
        "hk_phone": bool(getattr(active, "redact_hk_phone", True)),
        "email": bool(getattr(active, "redact_email", True)),
        "card": bool(getattr(active, "redact_card", True)),
        "patterns": {
            "hkid": shown("redact_hkid_pattern", "hkid"),
            "hk_phone": shown("redact_hk_phone_pattern", "hk_phone"),
            "email": shown("redact_email_pattern", "email"),
            "card": shown("redact_card_pattern", "card"),
        },
    }


def _runtime_view(active: Settings, store: Any | None = None) -> dict[str, Any]:
    jev_saved = bool(store and store.gate_secret_saved("jev"))
    fast_saved = bool(store and store.gate_secret_saved("fast"))
    poe_saved = bool(store and store.gate_secret_saved("poe"))
    return {
        "thresholds": {
            "router_confidence": active.router_threshold,
            "memory_similarity": active.memory_similarity_cutoff,
            "preplan_budget_seconds": active.preplan_budget_seconds,
            "max_repairs": active.max_repairs,
            "rule_passes": active.refine_max_rounds,
            "conversation_turns": active.conversation_turns,
            "reviewer_timeout_seconds": active.reviewer_timeout_seconds,
            "turn_deadline_seconds": active.turn_deadline_seconds,
            "llm_timeout_seconds": active.llm_timeout_seconds,
        },
        "redaction": _redaction_view(active),
        "reviewer": _reviewer_view(active, bool(store and store.gate_secret_saved("reviewer"))),
        "freshness_hours": {
            "weather": active.weather_fresh_seconds // 3600,
            "tickets": active.ticket_fresh_seconds // 3600,
            "hotels": active.hotel_fresh_seconds // 3600,
            "attractions": active.attraction_fresh_seconds // 3600,
        },
        "gate": "reason" if active.router_gate == "fast" else (active.router_gate if active.router_gate in {"jev", "plain", "reason"} else "jev"),
        "writer_model": active.writer_model,
        "poe_ready": bool(active.poe_api_key),
        "poe": _key_view(active.poe_api_key, poe_saved),
        "models": {
            "plain": active.gate_plain_model or "gemini-3.5-flash-lite",
            "reason": active.gate_reason_model or "gemini-3.8-flash",
        },
        "arms": {
            "plain": _arm_view(active, "plain", store.gate_secret_saved("plain") if store else False),
            "reason": _arm_view(active, "reason", store.gate_secret_saved("reason") if store else False),
        },
        "orchestrator": {
            "jev": {
                "model": active.decision_model,
                "url": active.decision_api_url,
                **_key_view(active.decision_api_key, jev_saved),
            },
            "fast": {
                "model": active.gate_fast_model,
                "url": active.gate_fast_base_url,
                **_key_view(active.gate_fast_api_key, fast_saved),
            },
        },
    }


def map_payload(plan: TripPlan) -> dict[str, Any]:
    def point(item) -> dict[str, Any] | None:
        if not item.geo:
            return None
        return {
            "id": item.id,
            "name": item.name,
            "type": item.type.value,
            "lat": item.geo.lat,
            "lng": item.geo.lng,
            "start": item.start,
            "end": item.end,
            "status": item.data_status.value,
            "sources": len(item.source_refs),
        }

    days = []
    for day in plan.days:
        points = [point(item) for item in day.items]
        days.append(
            {
                "date": day.date.isoformat(),
                "title": day.title,
                "weather": day.weather.model_dump(mode="json") if day.weather else None,
                "points": [p for p in points if p],
            }
        )
    hotel = plan.first_stay
    return {
        "trip_id": plan.trip_id,
        "destination": plan.destination.name,
        "center": plan.destination.geo.model_dump() if plan.destination.geo else None,
        "hotel": {
            "name": hotel.name,
            "lat": hotel.geo.lat,
            "lng": hotel.geo.lng,
        }
        if hotel and hotel.geo
        else None,
        # The cities in order and the stays, for a map of a trip of several; ``center`` and ``hotel`` are the first of each.
        "segments": [
            {
                "id": segment.id,
                "name": segment.destination.name,
                "center": segment.destination.geo.model_dump() if segment.destination.geo else None,
                "start": segment.dates.start.isoformat(),
                "end": segment.dates.end.isoformat(),
                "nights": segment.dates.night_count,
            }
            for segment in plan.segments
        ],
        "stays": [
            {
                "id": stay.id,
                "segment_id": stay.segment_id,
                "name": stay.name,
                "lat": stay.geo.lat if stay.geo else None,
                "lng": stay.geo.lng if stay.geo else None,
                "check_in": stay.check_in.isoformat(),
                "check_out": stay.check_out.isoformat(),
            }
            for stay in plan.hotels
        ],
        "days": days,
        "tickets": [
            {
                "id": ticket.id,
                "mode": ticket.mode,
                "name": ticket.name,
                "origin": ticket.origin,
                "destination": ticket.destination,
                "depart_at": ticket.depart_at.isoformat() if ticket.depart_at else None,
            }
            for ticket in plan.tickets
        ],
    }


app = create_app()
