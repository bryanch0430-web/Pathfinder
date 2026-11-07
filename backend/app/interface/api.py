"""HTTP and SSE interface. No business logic lives here."""

from __future__ import annotations

import asyncio
import hmac
import html
import json
import re
from contextlib import aclosing, asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Literal

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.application.legs import leg_options_for_plan, legs_for_plan
from app.application.orchestrator import user_facing_error
from app.application.preferences import derive_preferences
from app.application.guide_hybrid import retrieve_hybrid
from app.application.soft_guides import CORPUS_VERSION
from app.application.trending_rag import trending_report
from app.application.translation import SUPPORTED_LOCALES, LLMTranslator, TranslationService
from app.application.validation import tidy_data_gaps
from app.config import Settings, get_settings
from app.container import Container, build_container
from app.application.auth import create_token, decode_token, hash_password, verify_password
from app.domain.models import (
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
    TripContext,
    TripPlan,
    User,
    USER_PREFIX,
)
from app.evaluation import export_markdown
from app.interface.body_limit import BodyLimit
from app.interface.cache_control import PrivateResponses
from app.interface.caller_binding import CallerBinding
from app.interface.identity_format import TOKEN_FORMAT, TOKEN_MAX_LENGTH
from app.interface.rate_limit import RateLimits, SlidingWindowLimiter, rate_limit
from app.interface.sse import KEEPALIVE, KEEPALIVE_FRAME, TIMED_OUT, Deadline, paced, sse_frame

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
    preferences: list[FormLine] | None = Field(default=None, max_length=TRIP_LINES_MAX)
    hard_constraints: list[FormLine] | None = Field(default=None, max_length=TRIP_LINES_MAX)
    language: FormText | None = None
    plan_locale: Literal["en", "zh-Hant"] | None = None


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
    app.add_middleware(BodyLimit)
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
    )
    limit_auth = Depends(rate_limit("auth", "Too many sign-in attempts."))
    charge_ai = rate_limit("ai", "Too many planning requests.")
    limit_ai = Depends(charge_ai)
    # A turn, a new trip and a look-up are charged inside the route, once the request is known to do its
    # work, so one that is refused (401, 404, 422) costs nothing and cannot lock out the people it is not from.
    charge_create = rate_limit("create", "Too many new trips or share links.")
    charge_lookup = rate_limit("lookup", "Too many place look-ups.")
    charge_handbook = rate_limit("handbook", "Too many handbook look-ups.")

    def container(request: Request) -> Container:
        return request.app.state.container

    def require_admin(x_admin_token: str = Header(default="")) -> None:
        expected = settings.admin_token
        # An empty setting disables the admin endpoints instead of matching "".
        if not expected or not x_admin_token or not hmac.compare_digest(
            x_admin_token.encode("utf-8"), expected.encode("utf-8")
        ):
            raise HTTPException(status_code=401, detail="Admin token required")

    # -- meta -------------------------------------------------------------

    @app.get("/api/ready")
    async def health(request: Request) -> dict[str, Any]:
        return container(request).health()

    @app.get("/api/runtime")
    async def config(request: Request) -> dict[str, Any]:
        c = container(request)
        return {
            "app": settings.app_name,
            "version": "1.0.0",
            "default_locale": settings.default_locale,
            "locales": settings.supported_locales.split(","),
            "currency": settings.default_currency,
            "thresholds": {
                "router_confidence": settings.router_threshold,
                "memory_similarity": settings.memory_similarity_cutoff,
                "preplan_budget_seconds": settings.preplan_budget_seconds,
                "max_repairs": settings.max_repairs,
            },
            "freshness_hours": {
                "weather": settings.weather_fresh_seconds // 3600,
                "tickets": settings.ticket_fresh_seconds // 3600,
                "hotels": settings.hotel_fresh_seconds // 3600,
                "attractions": settings.attraction_fresh_seconds // 3600,
            },
            "writer": {
                "available": bool(getattr(c.llm, "available", False)),
                "model": getattr(c.llm, "name", "offline"),
                "label": settings.llm_provider,
                "vendor": settings.writer_vendor,
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
            party_size=2,
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
            "messages": [message.model_dump(mode="json") for message in c.store.list_messages(session_id, limit=60)],
        }

    @app.patch("/api/journeys/{session_id}/state")
    async def patch_context(session_id: str, payload: ContextPatch, request: Request) -> dict[str, Any]:
        c = container(request)
        require_session(c, request, session_id)
        context = c.store.get_context(session_id) or TripContext(session_id=session_id)
        was = (context.budget, context.currency.upper(), context.start_date)
        data = payload.model_dump(exclude_unset=True)
        for key, value in data.items():
            if value is not None:
                setattr(context, key, value)
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
        """Walk / cycle / transit / taxi for one leg; read-only comparison."""
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
                context = c.store.get_context(session_id)
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

    @app.post("/api/recall/clear")
    async def clear_memory(request: Request, payload: ClearMemoryRequest | None = None) -> dict[str, Any]:
        c = container(request)
        identity = resolve_identity(request, c, payload.client_token if payload else "")
        cleared = c.store.clear_memory(identity)
        return {"cleared": True, **cleared}

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
                    party_size=2,
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
                on_wait=deadline.pause,
                on_start=deadline.restart,
            )
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
                            yield sse_frame(event.event, event.data)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
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
    hotel = plan.hotels[0] if plan.hotels else None
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
