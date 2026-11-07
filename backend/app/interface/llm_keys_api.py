"""The /api/llm routes: a caller adds, tests, chooses and removes their own LLM keys.

The owner is the caller (``resolve_identity``), or the operator's ``server`` row with ``?scope=server`` and the
admin token. A key travels only in a JSON body, is stored encrypted, and leaves as its masked tail: no answer,
error or log line here repeats it. A refused body is answered by our own checks, never FastAPI's validation
answer, which repeats the value it refused.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass, field
from typing import Annotated, Any, Callable

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints

from app.config import Settings
from app.container import Container
from app.domain.credentials import SERVER_OWNER, StoredCredential
from app.domain.models import USER_PREFIX
from app.infrastructure.llm_providers import PROVIDERS, KeyTestResult, ProviderSpec, probe_key
from app.infrastructure.secret_box import UnreadableSecret, mask_key
from app.interface.identity_format import TOKEN_FORMAT, TOKEN_MAX_LENGTH

NO_SUCH_KEY = "No such key."
KEY_FORMAT_MESSAGE = "The key must be 8 to 512 printable characters."
UNREADABLE_MESSAGE = "This saved key can no longer be read. Remove it and add it again."
MODEL_MAX_LENGTH = 120
#: Printable ASCII without spaces; checked after stripping.
_KEY_FORMAT = re.compile(r"[!-~]{8,512}")
#: A failed test that still lets the key be saved, with a warning: it authenticated but could not run right now.
#: Every other kind, an unknown one included, is refused.
_SAVE_WITH_WARNING = {"rate_limit", "network", "credit"}

ModelName = Annotated[str, StringConstraints(strip_whitespace=True, max_length=MODEL_MAX_LENGTH)]


@dataclass
class KeyIn:
    spec: ProviderSpec
    api_key: str = field(repr=False)
    model: str
    use_for_search: bool = True


class KeyPatch(BaseModel):
    model: ModelName | None = None
    use_for_search: bool | None = None


class KeyChoice(BaseModel):
    key_id: Annotated[str, StringConstraints(max_length=64)] | None


def parse_key_in(body: Any, *, with_search: bool = False) -> KeyIn:
    """Read ``{provider, api_key, model[, use_for_search]}``. No refusal names or repeats a value it was sent."""
    if not isinstance(body, dict):
        raise HTTPException(status_code=422, detail="Send the provider, key and model as a JSON object.")
    spec = PROVIDERS.get(body["provider"]) if isinstance(body.get("provider"), str) else None
    if spec is None:
        raise HTTPException(status_code=422, detail="Choose one of the listed providers.")
    api_key = body.get("api_key")
    api_key = api_key.strip() if isinstance(api_key, str) else ""
    if not _KEY_FORMAT.fullmatch(api_key):
        raise HTTPException(status_code=422, detail=KEY_FORMAT_MESSAGE)
    model = body.get("model")
    if model is None:
        model = ""
    if not isinstance(model, str) or len(model.strip()) > MODEL_MAX_LENGTH:
        raise HTTPException(status_code=422, detail=f"The model name must be 1 to {MODEL_MAX_LENGTH} characters.")
    use_for_search = body.get("use_for_search", True) if with_search else True
    if not isinstance(use_for_search, bool):
        raise HTTPException(status_code=422, detail="use_for_search must be true or false.")
    return KeyIn(spec, api_key, model.strip() or spec.default_model, use_for_search)


def register_llm_key_routes(
    app: FastAPI,
    *,
    settings: Settings,
    container: Callable[[Request], Container],
    resolve_identity: Callable[..., str],
    require_admin: Callable[..., None],
    charge_key_test: Callable,
) -> None:
    def key_owner(request: Request, c: Container) -> str:
        """The server row for an admin asking with ``?scope=server``; otherwise the caller, who is never it."""
        if request.query_params.get("scope") == "server":
            require_admin(request.headers.get("x-admin-token", ""))
            return SERVER_OWNER
        owner = resolve_identity(request, c)
        if owner == SERVER_OWNER:
            # A client that names the operator's row as its token does not manage the operator's key.
            raise HTTPException(status_code=401, detail="Sign in or send a client token.")
        if not owner.startswith(USER_PREFIX) and not TOKEN_FORMAT.fullmatch(owner):
            # The request binding ignores such a token, so a key saved under it would never be used.
            raise HTTPException(
                status_code=422,
                detail=f"The guest token must be 1 to {TOKEN_MAX_LENGTH} printable ASCII characters without spaces.",
            )
        return owner

    def key_view(c: Container, row: StoredCredential) -> dict[str, Any]:
        try:
            c.box.decrypt(row.ciphertext)
            unreadable = False
        except UnreadableSecret:
            unreadable = True
        spec = PROVIDERS.get(row.provider)
        return {
            "id": row.id,
            "provider": row.provider,
            "label": spec.label if spec else row.provider,
            "model": row.model or (spec.default_model if spec else ""),
            "key_tail": row.key_tail,
            "active": row.active,
            "use_for_search": row.use_for_search,
            "last_test_ok": row.last_test_ok,
            "last_tested_at": row.last_tested_at,
            "last_error": row.last_error,
            "unreadable": unreadable,
        }

    def status_now(c: Container, owner: str) -> dict[str, Any]:
        selector = c.selector
        selection = selector.operator_chain() if owner == SERVER_OWNER else selector.for_caller(owner)
        return {
            "source": selection.source,
            "provider": selection.provider,
            "model": selection.model,
            # "unreadable" or "unknown_provider" when the chosen key cannot be used; "" when it can.
            "problem": selection.problem,
            "local": selector.local_status(),
        }

    async def status_view(c: Container, owner: str) -> dict[str, Any]:
        # In a thread: the local model's probe is a blocking request, once per probe TTL.
        return await asyncio.to_thread(status_now, c, owner)

    def owned_row(c: Container, owner: str, key_id: str) -> StoredCredential:
        row = c.store.get_credential(owner, key_id)
        if row is None:
            raise HTTPException(status_code=404, detail=NO_SUCH_KEY)
        return row

    async def run_test(request: Request, spec: ProviderSpec, api_key: str, model: str) -> KeyTestResult:
        await charge_key_test(request)
        return await probe_key(spec, api_key, model, settings)

    @app.get("/api/llm/providers")
    async def llm_providers() -> dict[str, Any]:
        return {
            "providers": [
                {
                    "id": spec.id,
                    "label": spec.label,
                    "default_model": spec.default_model,
                    "key_prefix": spec.key_prefix,
                    "supports_search": spec.supports_search,
                }
                for spec in PROVIDERS.values()
            ]
        }

    @app.get("/api/llm/keys")
    async def llm_keys(request: Request) -> dict[str, Any]:
        c = container(request)
        owner = key_owner(request, c)
        return {
            "keys": [key_view(c, row) for row in c.store.list_credentials(owner)],
            "status": await status_view(c, owner),
        }

    @app.post("/api/llm/keys/test")
    async def test_unsaved_key(request: Request, body: Any = Body(default=None)) -> dict[str, Any]:
        c = container(request)
        key_owner(request, c)
        key = parse_key_in(body)
        return asdict(await run_test(request, key.spec, key.api_key, key.model))

    @app.post("/api/llm/keys", response_model=None)
    async def save_key(request: Request, body: Any = Body(default=None)) -> dict[str, Any] | JSONResponse:
        c = container(request)
        owner = key_owner(request, c)
        key = parse_key_in(body, with_search=True)
        result = await run_test(request, key.spec, key.api_key, key.model)
        if not result.ok and result.kind not in _SAVE_WITH_WARNING:
            return JSONResponse(status_code=422, content={"detail": result.message, "kind": result.kind})
        row = c.store.add_credential(
            owner,
            key.spec.id,
            key.model,
            c.box.encrypt(key.api_key),
            mask_key(key.api_key),
            key.use_for_search,
            result.ok,
            "" if result.ok else result.message,
        )
        answer: dict[str, Any] = {"key": key_view(c, row)}
        if not result.ok:
            answer["warning"] = result.message
        return answer

    @app.post("/api/llm/keys/{key_id}/test")
    async def retest_key(key_id: str, request: Request) -> dict[str, Any]:
        c = container(request)
        owner = key_owner(request, c)
        row = owned_row(c, owner, key_id)
        spec = PROVIDERS.get(row.provider)
        if spec is None:
            raise HTTPException(status_code=422, detail="This key's provider is no longer offered. Remove the key.")
        try:
            api_key = c.box.decrypt(row.ciphertext)
        except UnreadableSecret:
            raise HTTPException(status_code=409, detail=UNREADABLE_MESSAGE) from None
        result = await run_test(request, spec, api_key, row.model or spec.default_model)
        updated = c.store.update_credential(
            owner, key_id, test_ok=result.ok, error="" if result.ok else result.message
        )
        if updated is None:  # removed while it was being tested
            raise HTTPException(status_code=404, detail=NO_SUCH_KEY)
        # No ``forget``: the key and model are unchanged, and a turn still running needs the cached key to scrub with.
        return {"key": key_view(c, updated), "result": asdict(result)}

    @app.patch("/api/llm/keys/{key_id}")
    async def change_key(key_id: str, payload: KeyPatch, request: Request) -> dict[str, Any]:
        c = container(request)
        owner = key_owner(request, c)
        row = owned_row(c, owner, key_id)
        model = payload.model
        if model == "":
            spec = PROVIDERS.get(row.provider)
            model = spec.default_model if spec else ""
        updated = c.store.update_credential(owner, key_id, model=model, use_for_search=payload.use_for_search)
        if updated is None:
            raise HTTPException(status_code=404, detail=NO_SUCH_KEY)
        # The next request builds the writer from the changed row.
        c.selector.forget(key_id)
        return {"key": key_view(c, updated)}

    @app.put("/api/llm/choice")
    async def choose_key(payload: KeyChoice, request: Request) -> dict[str, Any]:
        c = container(request)
        owner = key_owner(request, c)
        if not c.store.choose_credential(owner, payload.key_id):
            raise HTTPException(status_code=404, detail=NO_SUCH_KEY)
        return {"status": await status_view(c, owner)}

    @app.delete("/api/llm/keys/{key_id}")
    async def delete_key(key_id: str, request: Request) -> dict[str, Any]:
        c = container(request)
        owner = key_owner(request, c)
        if not c.store.delete_credential(owner, key_id):
            raise HTTPException(status_code=404, detail=NO_SUCH_KEY)
        c.selector.forget(key_id)
        return {"deleted": True}
