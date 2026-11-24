# External references and third-party work

This project is a rebuild of the Pathfinder interface. The list below records the
**external** GitHub work that informed or supplied part of the submission. Earlier
Pathfinder versions (v1.x and the v5 prototypes) belong to the same project and are
therefore **not** listed as third-party work.

## Repositories consulted or adapted

| Repository | Licence | How it was used |
|---|---|---|
| [watermelon588/Tripverse](https://github.com/watermelon588/Tripverse) | MIT | **Sketchbook presentation reference.** The sketch feature — a hand-drawn overview page plus one page per day, page navigation and SVG export — follows Tripverse's sketchbook idea. The `tv-sketch` class names in `frontend/src/design-system/sketch/` mark that lineage. Tripverse was also examined as a **benchmark comparison**, but its overall design (LangGraph backend, Supabase, its benchmark protocol) was **not** adopted; Pathfinder keeps its own FastAPI/React architecture and its own frozen evaluation suites. |
| [tt-a1i/archify](https://github.com/tt-a1i/archify) | Apache-2.0 | Generated the architecture diagrams in `docs/architecture/` (`pathfinder-v5-architecture-detailed.html` and the report figures). Archify's licence notice is embedded in the generated HTML. |
| [Cocoon-AI/architecture-diagram-generator](https://github.com/Cocoon-AI/architecture-diagram-generator) | Apache-2.0 | Upstream of Archify; credited in the embedded Archify notice. No source file of this repository is included separately. |
| [elementalsouls/Claude-BugHunter](https://github.com/elementalsouls/Claude-BugHunter) | MIT | **Audit methodology reference.** Its bug-hunting checklist informed the security review of this codebase (CORS, admin origin checks, injection fence, import screening, key handling). No Claude-BugHunter code or skill file is copied into the submission. |

No other GitHub repository supplied source code to this submission. In particular,
the UI is Pathfinder's own carried-over React app; the backend is rebuilt in this
repository.

## Core dependencies (all GitHub-hosted open source)

Exact versions below are the versions tested for this submission; the declared
ranges are in `backend/pyproject.toml` and `frontend/package.json`.

### Backend (Python 3.13.2 tested; requires 3.11+)

| Package | Tested | Project |
|---|---:|---|
| fastapi | 0.165.0 | https://github.com/fastapi/fastapi |
| uvicorn | 0.54.0 | https://github.com/encode/uvicorn |
| httpx | 0.28.1 | https://github.com/encode/httpx |
| pydantic | 2.14.4 | https://github.com/pydantic/pydantic |
| pydantic-settings | 2.16.3 | https://github.com/pydantic/pydantic-settings |
| pypdf | 6.21.4 | https://github.com/py-pdf/pypdf |
| tzdata | 2026.6 | https://github.com/python/tzdata |
| pytest (dev) | declared extra | https://github.com/pytest-dev/pytest |
| pytest-asyncio (dev) | declared extra | https://github.com/pytest-dev/pytest-asyncio |

### Frontend (Node 24.19 tested; engines `>=20.19`)

| Package | Version | Project |
|---|---:|---|
| react / react-dom | 18.3.1 | https://github.com/facebook/react |
| vite | 5.4.21 | https://github.com/vitejs/vite |
| typescript | 5.9.3 | https://github.com/microsoft/TypeScript |
| pptxgenjs | 4.0.1 | https://github.com/gitbrent/PptxGenJS |
| roughjs | 4.6.6 | https://github.com/pshihn/rough |
| perfect-freehand | 1.2.3 | https://github.com/steveruizok/perfect-freehand |
| storybook | 8.6.19 | https://github.com/storybookjs/storybook |

## Data and service providers (not GitHub source)

Google Maps Platform / Places, SerpAPI, DeepSeek (writer, verifier, search),
Poe/ChatAnywhere-compatible endpoints, Open-Meteo (weather, no key),
Wikipedia/Wikimedia and YouTube/Bilibili (optional). Their terms and paid tiers
apply; see the root README for configuration and which modes work without keys.
The 12-city curated place corpus in `backend/app/evidence/place_corpus.py` is
project data and replaces these providers in sandbox mode.
