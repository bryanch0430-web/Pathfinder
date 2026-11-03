# Pathfinder — handoff for the next agent

**Date:** 2026-10-30  
**Repository:** <https://github.com/Me0wX-LR/Pathfinder_v1>  
**Branch:** `main`  
**Project root:** `Pathfinder_v1/`  
**Verify current commit:** `git log -1 --oneline`

This repository is standalone. The old workspace project and its `master` history were
intentionally not included. The `obselete/` folder at the workspace root is archived and
must not be imported, built, or pushed here.

---

## What this project is

Pathfinder is an agentic trip planner:

- FastAPI + Pydantic + SQLite/WAL + SSE backend.
- React 18 + TypeScript + Vite + Storybook frontend.
- Typed turn pipeline: **screen → fence → understand → route → one of plan /
  modify / ask / clarify**.
- Four parallel evidence agents: attractions, hotels, weather, tickets.
- Provenance and freshness on every item; visible data gaps; no invented facts.
- Agentic UI: plan tabs, map playback, useful feedback, account menu, guide
  citations, share link, Markdown export, HKD/FX monitor.

## Current working state

Implemented and verified:

- Optional email/password auth:
  - scrypt passwords, HS256 bearer tokens.
  - guest mode remains; first login merges guest preferences and claims the session.
  - owned sessions are owner-only (404 for another user).
  - Login error messages are friendly, not raw JSON.
  - Account menu (avatar/name) → **My trips**, **Settings**, **Log out**.
- Multiple plan tabs:
  - `New trip` preserves the old plan and opens another session.
  - tab labels are persisted in `pathfinder.trips`.
  - active session pointer is `pathfinder.session`.
  - trash tab scraps only the active session.
  - Trending → Try this trip creates a new tab.
- Useful feedback:
  - top-right of the middle itinerary card.
  - Useful → 1–5 star picker → green saved animation.
  - Not useful → “Marked as not useful”.
  - derives preference tags from structured plan/context data.
- Map:
  - 1×/2×/4× speed; follow toggle.
  - token interpolates over the actual gap (`previous.end → next.start`).
  - route legend: travelled/ahead, mode colours, leg list.
- Local guide RAG:
  - ten versioned Markdown documents: Hong Kong (3), Japan (3), USA (4).
  - BM25 + BGE-small embeddings (local, no API key).
  - optional BGE reranker, default **off** because it added no measured gain.
  - citations, `last_verified`, stale badge, content hash and corpus version.
- Route order:
  - open-path 2-opt for each day.
- Sharing/export:
  - redacted read-only share page `/s/{slug}`.
  - Markdown export from structured fields.
- HKD default and live/static FX monitor.
- Layout: three columns aligned to the same viewport height; right frame has an
  always-visible rounded bottom corner and independent scroll.

## Verification status

Last verified:

```bash
cd backend && PYTHONPATH=. python -m pytest -q
# 47 passed
```

Frontend:

```bash
cd frontend
node node_modules/typescript/bin/tsc --noEmit
node node_modules/vite/bin/vite.js build
```

The app and Storybook were running at:

- <http://127.0.0.1:8100>
- <http://127.0.0.1:6007>

## Local setup on a new computer

```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# POSIX:   source .venv/bin/activate
pip install -r requirements.txt

# Optional local hybrid RAG (BGE-small + optional BGE reranker)
pip install -r requirements-rag.txt

cp ../.env.example ../.env
# fill in keys locally; never commit .env
```

Backend:

```bash
cd .
PYTHONPATH=backend python -m uvicorn app.main:app --port 8100 --host 127.0.0.1
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

Local data is intentionally **not** committed:

- `.env`
- `data/*.db`
- `.cache/` (BGE model cache, ~1.2 GB)
- `frontend/node_modules/`, `frontend/dist/`, `frontend/storybook-static/`

The first guide request downloads/loads BGE models and can take ~9 s. Subsequent
hybrid queries are ~10 ms; the optional reranker is ~260 ms.

## Demo accounts

Accounts live in the local SQLite DB, not in Git. Create them on a fresh clone:

```bash
cd backend
PYTHONPATH=. python scripts/seed_demo_accounts.py
```

| Email | Password |
|---|---|
| `testaccount1@pathfinder.test` | `Pathfinder123!` |
| `testaccount2@pathfinder.test` | `Pathfinder123!` |
| `testaccount3@pathfinder.test` | `Pathfinder123!` |

Change `AUTH_SECRET` and remove demo accounts before any public deployment.

## External keys needed for full live behaviour

Set these locally in `.env`:

- `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`
- `GOOGLE_PLACES_API_KEY`
- `GOOGLE_MAPS_API_KEY` (map + routes)
- `SERPAPI_KEY` (hotels/flights)
- `YOUTUBE_API_KEY` (optional notes)
- auth secret / demo secrets

Without keys, the agent falls back to gaps or static/seasonal data; never invent
a price, hour, flight or booking.

## RAG corpus

- Documents: `backend/app/guides/*.md`
- Loader/retriever: `backend/app/application/soft_guides.py`
- Hybrid: `backend/app/application/guide_hybrid.py`
- 30-query eval: `backend/app/guide_eval.py`
- Paraphrase ablation: `backend/app/guide_ablation.py`
- Policy: `docs/RAG_CORPUS.md`

Current ablation:

```text
lexical Top1 0.9167
BM25/dense/hybrid/rerank Top1 1.0
reranker ~264 ms, no gain over hybrid ~11 ms
```

Do not turn the reranker on by default until a larger/harder held-out set shows a
gain. Jev is still the System 1 route classifier, not a reranker.

## Remaining work (priority)

1. ~~Google Maps + Baidu map provider switch~~ **Superseded.** The map is Google Maps JS
   for every destination, with the key served from `/api/runtime`; Leaflet and the Baidu
   provider are gone (`backend/tests/test_api.py` asserts `/api/runtime` has no Baidu key).
2. Fault-injection + p50/p95/contract benchmark.
3. Larger RAG corpus, harder held-out queries, embeddings/cross-encoder/Jev
   ablation with measured gain.
4. OR-Tools route optimization with soft opening-hour windows and a real travel
   matrix; current 2-opt is distance-only and synthetic.
5. ICS/PDF export; share-link revocation and comments.
6. Auth hardening: password reset, email verification, rate limiting, optional
   Google OAuth. Google OAuth requires a client ID/secret and authorized origins.
7. Collaboration/expense split/journal/offline PWA; booking link-out only.

## Reporting a bug

File it as a GitHub issue with the **Bug report** form (`.github/ISSUE_TEMPLATE/bug_report.yml`). Do not add a row to a spreadsheet or a Markdown ledger. The account that clicks **Submit new issue** is the reporter; do not type a name.

Do this once, on the tree you actually ran. `git rev-parse --short HEAD` is the commit. Say `main` unless you reproduced it on another branch.

1. Quote the traveller's sentence and the selected chip. If no chip was selected, write `none`. Do not paraphrase the sentence.
2. Write numbered steps, what the plan and the reply should have done, and what they did. Name the row that changed and the rows that should have stayed.
3. Take the `request_id` for that turn. It looks like `req_` plus 12 hex characters.
   - In the browser, open the request to `POST /api/turn/stream`. In the event stream, the event named `trace` has `request_id`. The same id is on the final `done` event.
   - On disk the file is `data/traces/<request_id>.json`. One turn writes one file when the trace finishes. `GET /api/admin/spans/<request_id>` returns that file and requires `X-Admin-Token`.
4. Paste a short log, not the whole file. From the JSON, copy `request_id`, `status`, `duration_ms`, and for each span its `name`, `attrs.status`, and `duration_ms`. From `generations`, copy `purpose`, `model`, and `latency_ms`. If the API process printed a traceback for that request, paste the traceback too.
5. If the bug is a 404 or a wrong path, also paste the request path and the status code from the browser network panel. That is the log. A missing `request_id` is allowed only when the server never started the turn; write `none` and say so.
6. A proposed fix is optional. If you have one, write the check the code should enforce and what it must still refuse. Do not write "make it smarter".

Before you paste, delete anything that is a secret: values from `.env`, `Authorization`, `X-Admin-Token`, cookies, and API keys. Keep the traveller's sentence. Do not attach `data/pathfinder.db`.

## Important invariants

- Active project is `Pathfinder_v1/`; never commit root `obselete/`.
- Never commit `.env` or local databases.
- Keep volatile facts in live agents; RAG is soft context only.
- Every claim needs a source/freshness label; gaps stay visible.
- English-first UI, code and docs.
- Run backend tests before commit; commit and push to `origin/main`.

## Push state

The repository was pushed to:

```text
https://github.com/Me0wX-LR/Pathfinder_v1
branch: main
```

If a new computer has a different remote, set:

```bash
git remote set-url origin https://github.com/Me0wX-LR/Pathfinder_v1.git
```
