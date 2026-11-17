# Pathfinder — local deployment

Installed from <https://github.com/bryanch0430-web/Pathfinder> into this folder
(`Pathfinder_Router Version_16_11`), running on this machine only.

## Open it

| What | URL |
|---|---|
| App | <http://127.0.0.1:8100> (also <http://localhost:8100>) |
| API docs (Swagger) | <http://127.0.0.1:8100/docs> |
| Readiness | <http://127.0.0.1:8100/api/ready> |

Use `localhost` or `127.0.0.1`. Local admin mode requires the request to name this machine,
so a LAN address (`192.168.x.x`) is refused by design.

## Start and stop

```powershell
.\start.cmd              # start (serves the built frontend from frontend\dist)
.\start.cmd -Rebuild     # rebuild the frontend first
.\start.cmd -Port 8110   # another port
```

`start.cmd` calls `start.ps1` with `-ExecutionPolicy Bypass`, because this machine's
PowerShell policy disables scripts. To run the PowerShell script directly, do the same:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

Stop a running server:

```powershell
Get-NetTCPConnection -LocalPort 8100 -State Listen |
  ForEach-Object { Stop-Process -Id $_.OwningProcess }
```

Development mode with hot reload (two terminals — Vite on 5173 proxies /api to 8100):

```powershell
.\scripts\dev.ps1 backend
.\scripts\dev.ps1 frontend
```

## What is installed

- `backend\.venv` — Python 3.13 virtual environment with `backend\requirements.txt`
  (FastAPI, uvicorn, pydantic, cryptography, pypdf, pytest).
- `frontend\dist` — production build of the React console, served by the backend at `/`.
- `data\` — created at start: SQLite database, request traces, evaluation output. Git-ignored.
- `.env` — the configuration you supplied, at the repository root (git-ignored).

The backend reads `.env` from the repository root, then `backend\.env` if it exists.

## Configuration in use

- `ENVIRONMENT=development`, `LOCAL_ADMIN=true` — no sign-in on this machine; the
  evaluation pages, the threat panel and the server key card are open to loopback callers.
- Writer for testing: an **OpenAI-compatible endpoint** —
  `LLM_PROVIDER=openai`, `OPENAI_BASE_URL` and `OPENAI_MODEL` in `.env`,
  currently `gpt-6.1-terra`. The xAI block is still in `.env`; set `LLM_PROVIDER=xai`
  to switch back, nothing else changes.
- Web search: DeepSeek; Grok search off. Judge: `deepseek-chat`.
- System 1 router: typesafe.ai `jev-latest` (1.5 s timeout, rules as fallback).
- Tracing: Langfuse `https://us.cloud.langfuse.com`.
- Providers: Google Places/Maps/Routes, SerpApi (hotels, flights), YouTube, Bilibili,
  Open-Meteo, Wikimedia.

Verified working: `/api/ready` reports every provider available and the writer as
`gpt-6.1-terra` (`openai-compatible`), and a live planning turn ran through that endpoint —
the trace names `global.openai.gpt-6.1-terra`, the gateway's own model string.

## Switching provider

Anything that serves `POST /chat/completions` is a drop-in writer. Edit `.env` and restart:

```bash
LLM_PROVIDER=openai          # or openai-compatible / compatible / custom
OPENAI_BASE_URL=https://host/v1
OPENAI_API_KEY=sk-...
OPENAI_MODEL=the-model-id-that-endpoint-serves
```

`openai`, `openai-compatible` (also `openai_compatible`, `openai compatible`), `compatible`
and `custom` read the `OPENAI_*` block and fall back to `LLM_*` for any blank field — the
same rule the `XAI_*` block follows. Use the model *id* from `GET {OPENAI_BASE_URL}/models`,
not a display name.

A traveller can also save their own key without touching `.env`: in **Settings → Model and
API keys**, choose **OpenAI-compatible API**, paste the base URL, the model id and the key.
That URL is stored per key and called in place of any fixed vendor address.



`LOCAL_ADMIN` and the built-in secret defaults are development-only; the backend refuses to
start with `LOCAL_ADMIN` set under any other `ENVIRONMENT`. For any shared or public
deployment, set:

- `ENVIRONMENT=production`
- `AUTH_SECRET` — random, 32+ characters
- `ADMIN_TOKEN` — a real secret, not `local-admin`
- `BYOK_ENCRYPTION_KEY` — a Fernet key
- `GOOGLE_MAPS_API_KEY` — restricted by HTTP referrer

Also rotate the API keys that were shared in plain text, and drop `LOCAL_ADMIN`.
