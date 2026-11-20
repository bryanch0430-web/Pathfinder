# Pathfinder

**Version 1.1**

Pathfinder is a local travel planner. A conversation drafts a day-by-day itinerary, and the studio shows that itinerary as a plan, a sketch, a map, and a calendar. The planner runs on your machine. It can look up places, weather, and routes. It does not reserve flights, hotels, or restaurants.

Version 1.1 is the current release. Version 1.0, the 16 November 2026 local package, is listed under it.

| Version | Area | What the release does |
| --- | --- | --- |
| 1.1 | Places | Restaurants, cafes, sights, and lodging come from Google Places. Hotels can also come from SerpApi when `SERPAPI_KEY` is set. The area panel still includes Wikivoyage and Wikipedia. |
| 1.1 | Edits | A named swap replaces that stop, and a later request can restore it. A title with no mappable venue is replaced with a real place or removed. A day that changes city names the journey and a hotel in the arrival city. |
| 1.1 | Routes | With `GOOGLE_ROUTES_ENABLED` and a Google key, a leg under 2.5 km is walked, a leg under 35 km uses transit, and a longer leg is driven. A leg over 2,500 km, such as a flight, stays a straight line. |
| 1.1 | Map and calendar | The studio map can play through the stops of the selected day. The calendar export includes stays and route legs, and route legs are marked as estimates. `GET /api/trips/{id}/calendar.ics` returns the file. |
| 1.1 | Weather | Open-Meteo covers today through the next 15 days. A later date uses the three-year average of that calendar date and is labeled as a seasonal average. MET Norway fills a missing forecast day. NASA POWER monthly climate is the last fallback. |
| 1.1 | Tickets | Day 1 opens with the outbound flight and the last day closes with the return. Each one is its own one-way Google Flights link, labeled Ticket outbound and Ticket inbound. Nothing is reserved. |
| 1.1 | Conversation | A follow-up can change the days and stops it names and leave the rest of the plan in place. The router stays off unless `ROUTER_ENABLED` is true. Jev is used only when a decision URL and key are set and decision routing is enabled. Personal details can be masked before a model sees them. |
| 1.1 | Studio and accounts | The budget can be shown in the trip currency, including HKD. A desk page lists chats waiting for a person. An administrator is created with `scripts/create_admin.py`. Guest planning remains the default. The interface is in English. |
| 1.0 | Planner | DeepSeek plans the trip through a LangGraph workflow. The trip is stored in SQLite. |
| 1.0 | Studio | The studio shows the itinerary. Travel times are often straight-line estimates. |
| 1.0 | Places | A stop can be a theme line with no venue on a map. Live place search sits outside the planner. |
| 1.0 | Weather and tickets | Weather answers and flight booking links sit outside the planner. |
| 1.0 | Accounts | A visitor can plan as a guest or sign in with a local account. |

## Requirements

- Node.js 20 or newer. Node.js 24 is the version used in development.
- Python 3.12 or newer.
- pnpm 11, or `npx pnpm@11.25.0`.

Install dependencies after checkout. `node_modules` and the Python virtual environment are not part of the source tree.

## Configuration

Copy each example file and fill in keys there. Do not put provider keys in frontend source.

| File | Who reads it | What belongs there |
| --- | --- | --- |
| `backend/.env` | The API process | DeepSeek, database, CORS, Places, Routes, SerpApi, YouTube |
| `frontend/.env` | The browser, via Vite | API address, guest mode, the Maps JavaScript key |

Vite exposes a variable to the page only when the name starts with `VITE_`. The map is drawn in the browser, so `VITE_GOOGLE_MAPS_API_KEY` has to live in `frontend/.env`. Place search and road routing run on the server and use `GOOGLE_PLACES_API_KEY` and `GOOGLE_MAPS_API_KEY` in `backend/.env`. The same Google key may be entered in both files. The browser copy is visible to anyone who loads the page. Restrict that key by HTTP referrer before a public deployment.

The supported local pair is API port **8000** and the page on port **5174**.

`backend/.env.example` already allows `http://localhost:5174` and `http://127.0.0.1:5174`. `frontend/.env.example` points `VITE_API_URL` at `http://localhost:8000`. If you change either port, update both `VITE_API_URL` and `CORS_ORIGINS`.

`LLM_PROVIDER=deepseek` is the default model setting. Put the DeepSeek key only in `backend/.env`.

## Run

From `backend`:

```bash
python -m venv .venv
```

macOS or Linux:

```bash
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Health check: [http://127.0.0.1:8000/api/health](http://127.0.0.1:8000/api/health). A ready API returns `"status": "healthy"` and a SQLite database.

From `frontend`, in a second terminal:

```bash
npx pnpm@11.25.0 install --frozen-lockfile
npx pnpm@11.25.0 exec vite --host 127.0.0.1 --port 5174 --strictPort
```

Open [http://127.0.0.1:5174/create](http://127.0.0.1:5174/create) for guest planning. Stop either process with Ctrl+C.

`npm run dev` inside `frontend` uses Vite's default port 5173, which is not in the example CORS list. Use port 5174, or add the origin you actually open to `CORS_ORIGINS`.

## Accounts

Guest mode needs no account. Sign-in uses the local user table in `backend/pathfinder.db`.

Create or update an administrator from `backend`:

```bash
.venv/bin/python scripts/create_admin.py --email you@example.com --name "Your Name"
```

Windows:

```powershell
.\.venv\Scripts\python.exe scripts\create_admin.py --email you@example.com --name "Your Name"
```

Omit `--password` and the script generates one and prints it once. Sign in from the page, or open `/profile`. Trips planned as a guest are claimed by the account on sign-in. Changing `LOCAL_AUTH_SECRET` signs every session out.

## Trips already in the database

`backend/pathfinder.db` is local SQLite and is not part of a fresh install. Guest identity is stored in the browser under `tripverse_guest_id`. A new browser creates a new guest, so older trips stay in the database until that guest id is set again. Create a new trip if you do not need the old ones.

## Tests

From `backend`, with the virtual environment active:

```bash
python -m pytest
```

From `frontend`:

```bash
npx pnpm@11.25.0 test
npx pnpm@11.25.0 exec tsc --noEmit
```

## License

Pathfinder is released under the MIT License. See [LICENSE](LICENSE).
