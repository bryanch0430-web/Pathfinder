# Bring your own LLM key (BYOK): design

Date: 2026-11-08. Status: draft for approval. Branch: `worktree-byok`.

## Intent

Today PathFinder has exactly one writer model. The operator configures it in `.env`
(`LLM_*` / `XAI_*`), and it is built once per process (`backend/app/container.py:87`).
Anyone running PathFinder locally needs that operator key, or they get the no-model path.

This change lets the person at the browser paste their own key in Settings, test it, pick a model,
and have PathFinder use it. Supported providers: Anthropic Claude, OpenAI GPT, Poe, xAI Grok and
Google Gemini. With no key, PathFinder uses a local model (Ollama or another OpenAI-compatible
local server). With neither, it uses today's deterministic `NullLLM` path.

User's framing (2026-11-08): keep it simple, a place to add keys, **no account sign-in needed;
this is for local testing**.

## Decisions

The user made these:

1. **Scope.** Keys belong to whoever is calling, with no sign-in required. The frontend already
   sends a per-browser guest token (`X-Client-Token`) on every request, so a key added in a
   browser belongs to that browser. If someone is signed in, the existing identity resolver
   (`resolve_identity`, `interface/api.py:375`) gives `user:<id>` instead, and the key belongs to
   the account. That costs nothing extra, but no account UI is built or required.
2. **Operator key.** The `.env` key stays exactly as today. The admin token (`X-Admin-Token`,
   already stored by the frontend under the localStorage key `admin`) also shows a **Server key**
   card in Settings. A key saved there overrides the `.env` key for everyone who has no key of
   their own.
3. **Purposes.** A traveller's key powers:
   - every writer call through `LLMGateway`: planning, chat, edits, the fallback `LLMRouter`,
     translation, clarifier and ask;
   - **Grok web search**, when the traveller has saved an xAI key.

   The System 1 decision model (`decision_*`) and the eval judge (`judge_*`) stay operator-only.
   DeepSeek search also stays operator-only.
4. **Failure.** If the traveller's chosen key fails at run time (401/403, out of credit,
   rate-limited, network), the turn fails with a plain message naming their provider and pointing
   at Settings. PathFinder **never silently falls back** to the operator key or the local model.

These were my calls, stated to the user without objection:

5. **Storage.**
   - Keys live in SQLite, encrypted with Fernet (new dependency: `cryptography`).
   - The encryption key comes from a new setting, `BYOK_ENCRYPTION_KEY`, a Fernet key.
   - In development and test, an empty value falls back to a key derived from `auth_secret` (SHA-256).
   - Outside development and test, `Settings.check_secrets` refuses an empty value, the same way it
     handles `AUTH_SECRET`.
   - A row that no longer decrypts (for example after the secret changed) shows as "unreadable,
     re-add it". It never crashes.
6. **Masking.**
   - The browser only ever receives a masked tail (`sk-…a1b2`: up to the first 3 characters,
     an ellipsis, then the last 4).
   - A key is never placed in a URL, log line, trace file, Langfuse span, `emit_live` event, error
     text or API response.
   - Provider error bodies are scrubbed of the key before they reach `ProviderError.detail`.
7. **Test before save.**
   - **Save** always runs a tiny test call first (a few-token completion against the chosen
     model) and refuses a key that fails.
   - A separate **Test** button re-tests saved keys, and also tests an unsaved key without saving it.
   - A successful test also fetches the provider's model list, where the provider offers one, to
     fill the model dropdown.
8. **Local model.**
   - Only the operator configures it (`LOCAL_LLM_BASE_URL`, `LOCAL_LLM_MODEL`, optional
     `LOCAL_LLM_API_KEY`).
   - Travellers cannot enter a server address, because that would let any browser make the server
     call internal addresses (SSRF).
   - `.env.example` suggests Ollama at `http://localhost:11434/v1`. The code default is empty
     (off), so tests and fresh installs make no surprise network calls.
9. **Resolution order** for a call made on behalf of a caller:
   1. the caller's chosen key;
   2. the server key from the admin card;
   3. the `.env` writer key;
   4. the local model, if configured and reachable;
   5. `NullLLM`.

   Steps 2 to 5 together are the "operator chain". If the caller has a chosen key, steps 2 to 5
   are never used for that caller (see decision 4).

These were settled during the builder check (my calls, flagged for the user):

10. **No fall-through anywhere at run time.** Resolution chooses one source before the call. The
    first configured source wins, and the local model counts only when its probe says it is
    reachable. If that source fails during the call, the call fails. A bad server key does not
    quietly drop to the `.env` key. This matches what happens today when the `.env` key fails.
11. **Lazy resolution.** The middleware binds only the caller identity. The credential is read
    and decrypted the first time that request makes an LLM or Grok call, then memoised for the
    request. Static routes and polling routes never touch the table.
12. **Sign-in adoption.** When a guest signs in, the existing adoption paths
    (`adopt_guest_memory` / `adopt_guest_sessions`, `interface/api.py` around line 400) also move
    the guest's keys to the account. If the account already has an active key, the adopted keys
    arrive inactive. No account UI is built for this. It only stops a key vanishing.
13. **Test strictness.**
    - Save refuses on AUTH, INVALID_REQUEST or "model not found".
    - On RATE_LIMIT or out-of-credit (the key authenticated but could not run), save is allowed
      with a visible warning.
    - The bar: every provider's default model must pass the test. Any other model is best-effort.
    - `OpenAICompatibleLLM` extends its existing "retry without `response_format`" pattern: one
      retry with `max_completion_tokens` in place of `max_tokens`, and without `temperature`, when
      a 400 names those fields. OpenAI's newer models need this.
14. **Mid-turn failure** follows the existing error paths, with no pre-flight check and no
    planner change.
    - Several writer callers already swallow writer errors and fall back to their rule-based path.
      For example, `Planner._draft` has `except Exception: return None` at `planner.py:493`.
    - So decision 4's "show the error" works like this. The first failure of a caller key in a
      request is remembered for that request. Later calls in the same request fail fast instead of
      retrying a dead key.
    - The failure is streamed as an `llm_key` live event: `{provider, model, reason}`, scrubbed.
      The chat shows it as a visible notice: "Your Anthropic key failed (invalid key). This answer
      used PathFinder's rule-based path. Fix the key in Settings."
    - Where the error does reach `user_facing_error` (`orchestrator.py:77`), it gets its own message
      naming the provider instead of the generic "a source failed". This is a one-branch addition,
      not a refactor.
    - No other model or key is ever used.
15. **BYOK without a decision model** is the same as today's setup with no `DECISION_*` key:
    rules first, then `LLMRouter` through the gateway, which now uses the caller's key. One
    acceptance test covers "caller key only, no `.env` keys".
16. **Guest tokens** are browser UUIDs (`usePathfinder.ts:50`). That is enough for the
    local-testing scope the user set. A saved key can be spent, but never read, by anyone holding
    the token.

## Goals

- Add, test, choose and remove keys from the Settings page, for all five providers.
- The writer uses the chosen key on the very next request, with no restart.
- Two browsers with different keys use different keys at the same time on one server.
- The live app (`pathfinder.bat`) keeps working unchanged with the existing `.env` and no BYOK
  keys saved.

## Non-goals

- Account sign-up or sign-in work, and per-account key sync UI.
- Choosing a different model per purpose (writer vs router vs translation): one chosen
  key/model drives every writer purpose.
- Usage metering or billing per key, spending caps, quotas.
- BYOK for the decision model, the judge or DeepSeek search.
- Travellers configuring a local server address.
- Rotating `BYOK_ENCRYPTION_KEY` automatically. This is documented as "re-add your keys".

## Anti-goals (a failure even if it technically works)

- Any full key visible anywhere outside the encrypted column and the outbound request header.
- Spending the operator's key when the traveller believes their own key is in use.
- Broad refactors of the planner or orchestrator. Another session is changing them for
  multi-destination trips. The selection must be invisible to them.

## Constraints

- Tests make no real LLM or network calls. Use `httpx.MockTransport`. `infrastructure/http.py:73`
  already has a `_TRANSPORT` ContextVar, and the LLM adapters may adopt the same hook.
- Every new setting appears in `.env.example` (`tests/test_env_example.py`).
- Database changes go through a new append-only entry in `MIGRATIONS` (`infrastructure/store.py:343`).
- Commits are plain sentences with no Claude attribution. The author is `bryanch0430-web`.
- Backend suite: `cd backend && ENVIRONMENT=test PYTHONPATH=. .venv/Scripts/python.exe -m pytest -o addopts="" -q`
  (about 2954 passing today). The frontend has `tsc` typecheck, `node:test` (`src/lib/*.test.mjs`)
  and Storybook.

## How it works

### Providers

There is one catalogue, `backend/app/infrastructure/llm_providers.py`. Each entry has an id, label,
wire format, base URL, default model, a key hint (expected prefix, used only for a friendly
warning) and `supports_search` (xAI only).

| id | label | wire | base URL |
|---|---|---|---|
| `anthropic` | Anthropic Claude | anthropic | `https://api.anthropic.com/v1` |
| `openai` | OpenAI GPT | openai | `https://api.openai.com/v1` |
| `poe` | Poe | openai | `https://api.poe.com/v1` |
| `xai` | xAI Grok | openai | `https://api.x.ai/v1` |
| `gemini` | Google Gemini | openai | `https://generativelanguage.googleapis.com/v1beta/openai` |

- The "openai" wire reuses `OpenAICompatibleLLM`. It gains explicit `(base_url, api_key, model)`
  constructor arguments. The default still reads from `Settings`, so existing callers are untouched.
- `AnthropicLLM` is a new adapter for `POST /v1/messages`:
  - headers `x-api-key` and `anthropic-version: 2023-06-01`;
  - the system prompt goes in the top-level `system` field;
  - it sends `max_tokens` and `temperature`;
  - it has no JSON mode, so when `json_mode` is on it appends one instruction to return only a JSON
    object (the gateway already extracts JSON robustly);
  - the text comes from the `text` content blocks, and any `thinking` blocks fill `reasoning`;
  - usage comes from `usage.input_tokens` and `usage.output_tokens`;
  - status codes map to `FailureKind` exactly as `classify_status` does, with the same retry rules.
- Default models are a starting value only. The builder checks each one against that provider's
  current model list when implementing. xAI's default is the writer the live backend already uses:
  `grok-4.20-0309-non-reasoning`. The model field is always editable, and a successful test fills
  it with the provider's own list.

### Storage

New table `llm_credentials`, added by the next migration:

| column | notes |
|---|---|
| `id` | random hex id |
| `owner` | caller identity (guest token or `user:<id>`), or the literal `server` |
| `provider` | catalogue id |
| `model` | |
| `ciphertext` | Fernet token of the key |
| `key_tail` | the masked display string, computed at save time |
| `active` | at most one active row per owner |
| `use_for_search` | xAI only; default 1 |
| `last_test_ok`, `last_tested_at`, `last_error` | `last_error` is already scrubbed and short |
| `created_at`, `updated_at` | |

`SecretBox` (`backend/app/infrastructure/secret_box.py`) owns `encrypt` and `decrypt`. Only the
resolver and the test path ever call `decrypt`.

### Per-request selection

The gateway keeps one `llm` object, as today. The container now gives it a `CallerLLM` instead of
the fixed adapter. `CallerLLM` implements the `LLM` port and delegates each `complete()` to the LLM
bound for the current request in a ContextVar. This follows the same pattern as `TracerProxy`
(`observability.py:353`). With no binding (CLI, evals, tests that build the container directly),
it uses the operator chain. `available` and `name` are resolved the same way, so the existing
`gateway.available` checks keep working unchanged.

- A pure ASGI middleware binds the caller for each HTTP request. It reads the
  `Authorization: Bearer` header (a valid token gives `user:<id>`), otherwise the
  `X-Client-Token` header, and sets the ContextVar for the whole request. The caller's active
  credential is resolved lazily (decision 11): read, decrypted and turned into an adapter (built
  or reused) the first time the request makes an LLM call.
  - Because it is pure ASGI, streaming responses (`/api/turn/stream`), `BackgroundTasks` and
    `asyncio.create_task` children (`orchestrator.py:836`, `:894`) inherit it.
  - Callers that send the guest token only in a request body get the operator chain. The frontend
    always sends the header (`frontend/src/lib/api.ts:197`).
- Adapters for credentials are cached in a small LRU keyed by `(credential id, updated_at)`, so a
  changed model or key takes effect on the next request.
- The same binding carries the caller's xAI search key and model (an xAI credential with
  `use_for_search`, active or not).
  - `GrokSearchSource` reads the key and model per call: the caller's xAI key if present, else the
    operator's `XAI_*`.
  - Its `available` becomes dynamic: the operator has enabled Grok search with a key and model, or
    the caller has an xAI search key.
  - The registry always includes the Grok source, so a traveller's key can light it up when the
    operator flag is off. Consumers that test `web_deep` for emptiness must check `.available`
    instead (`asking.py:281`, `web_lookup.py:111` and `:116`).
- Run-time failure of a caller key: the adapter raises `ProviderError`. Its user-facing message
  names the provider and says to check the key in Settings. The detail is scrubbed. There is no
  fallback (decision 4).

### Observability

- Traces and `emit_live` keep the model name and add `llm_source`: `byok`, `server`, `env`,
  `local` or `none`. Traces never record a key or its tail.
- A unit-tested `scrub(text, secrets)` helper removes the key from any provider error text before
  it is stored or raised.

### API (`/api/llm/...`, JSON bodies only, keys never in a URL)

The caller is resolved with the existing `resolve_identity`. Adding `?scope=server` requires the
admin token and acts on the `server` owner.

- `GET /api/llm/providers`: the catalogue, without base URLs.
- `GET /api/llm/keys`: the caller's masked keys, plus a status block. The status names which
  source and model would serve this caller right now, and the local model's state (configured,
  reachable, model).
- `POST /api/llm/keys/test`: body `{provider, api_key, model}`. Tests without saving. Returns
  `{ok, latency_ms, models, error}`.
- `POST /api/llm/keys`: body `{provider, api_key, model, use_for_search?}`. Tests first, and on
  failure returns 422 with the scrubbed error. Saves and makes the key active. Returns the masked
  record.
- `POST /api/llm/keys/{id}/test`: re-tests a stored key.
- `PATCH /api/llm/keys/{id}`: `{model?, use_for_search?}`.
- `PUT /api/llm/choice`: `{key_id: str | null}`. `null` means "use the default" (the operator chain).
- `DELETE /api/llm/keys/{id}`.
- An owner can only see or touch their own rows. Someone else's id returns 404.
- `api_key` must be 8 to 512 printable characters.
- The test endpoints are rate-limited per caller, a few calls a minute, because they spend real tokens.

`/api/ready` and `/api/runtime` keep their shape. The writer block also reports `source`
(`server`, `env`, `local` or `none`) for the operator chain.

### Settings UI

A new **Model & API keys** card goes in `SettingsView` (`frontend/src/design-system/organisms.tsx`),
above Providers. The existing "Writer availability" line moves into it or reads from it.

- A status line in plain words, for example:
  - "Using your Anthropic key · claude-sonnet-5-5"
  - "Using the server key"
  - "Using the local model llama3.1"
  - "No model: PathFinder uses its rule-based path"
- Saved keys, one row each, showing:
  - provider and masked tail;
  - a model selector (a free-text input with a datalist of fetched models);
  - an "in use" radio;
  - a **Test** button with the last result and time;
  - for xAI, a "Also use for Grok web search" checkbox;
  - **Remove**, with a confirm step.
- A "Use the default" radio sits alongside the saved keys.
- An **Add key** form with:
  - a provider select;
  - a password-type key input with a show toggle;
  - a model input, prefilled with the default and filled from the test;
  - **Test** and **Save** buttons.

  The key input is cleared after save and never written to localStorage.
- With the admin token present, a second **Server key** card uses the same component with
  `scope=server`.
- New functions in `frontend/src/lib/api.ts`. The design-system rules apply (`docs/DESIGN_SYSTEM.md`).
  Add a Storybook story for the card.

### Docs

- `docs/ARCHITECTURE.md`: a BYOK section covering resolution order, storage and the "never logged" rules.
- `docs/API.md`: the new routes.
- `README.md`: how to add a key and how to set up Ollama.
- `.env.example`: `BYOK_ENCRYPTION_KEY` (with a one-line command to generate it),
  `LOCAL_LLM_BASE_URL`, `LOCAL_LLM_MODEL` and `LOCAL_LLM_API_KEY`.
- Changelog entry.

## Tests (minimum)

- `SecretBox`: round-trip; a wrong key gives "unreadable", not a crash; production refuses an
  empty `BYOK_ENCRYPTION_KEY`.
- Migration adds the table, and the existing DB upgrade test still passes.
- `AnthropicLLM` request shape and response parsing, and an error mapping test, all with a mock
  transport. `OpenAICompatibleLLM` with explicit args hits the provider base URL with the key in
  the header only.
- Resolution order across caller key, server key, env key, local model and `NullLLM`. A failing
  caller key raises and does not fall back.
- Middleware isolation: two clients with different `X-Client-Token`s and different keys, in the
  same app, each reach their own mock provider. A streaming turn uses the caller key.
- API: CRUD, masking, owner isolation (404 across owners), admin scope (403 without the token),
  test-before-save refusal, `PUT /choice null`.
- Leak test: a sentinel key string never appears in API responses, trace files, captured logs,
  `emit_live` events or the plain columns of the DB.
- Grok search uses the caller's xAI key when present, and the operator key otherwise.
- `.env.example` covers the new settings.

## Left to the builder

- Exact module names and file splits beyond those named above.
- The rate-limit mechanism for the test endpoints (reuse any existing limiter).
- How the local model's reachability probe is cached (a short-timeout `GET /models`, cached about
  60 s, is the suggestion).
- Card layout details within the design system.
