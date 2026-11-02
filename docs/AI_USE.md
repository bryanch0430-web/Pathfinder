# AI-use declaration

## Runtime models

| Role | Model / service | Where |
|---|---|---|
| Writer: understand, route fallback, planner draft, modify extraction, ask phrasing, clarify, repair | OpenAI-compatible chat model; reference `grok-4.20-0309-non-reasoning` via xAI | `backend/app/infrastructure/llm.py` |
| System 1 router (primary when configured) | TypeSafe Jev, `jev-latest` | `backend/app/infrastructure/decision.py` |
| Judge (evaluation only, optional) | OpenAI-compatible model; reference `deepseek-chat` (DeepSeek) | `backend/app/infrastructure/judge.py`; disabled unless `JUDGE_API_KEY` is set |
| Embeddings | Local signed hashing, 192 dimensions | `backend/app/application/memory.py` |

Prompts are committed as files in `backend/app/prompts/` (`router.md`, `understand.md`,
`planner.md`, `modify.md`, `ask.md`, `clarify.md`, `repair.md`). They are English-first and
carry a language rule and a canary sentinel.

## Development assistance

This repository was produced with an AI coding agent (DeepSeek Harness,
`deepseek-flash`) under explicit human direction. The agent:

- read the group's proposal, the functional specification and the course guideline;
- wrote the backend layers, the React design system, the tests, the evaluation
  harness and the docs;
- ran the tests, built the frontend and Storybook, and exercised live plan, ask and
  modify turns.

Product direction, design decisions, the demo and the final review are the **human
owner's**. Every runtime prompt is committed under `backend/app/prompts/`.

## Development artifacts

- the functional baseline: `docs/SPEC.md`;
- every runtime prompt: `backend/app/prompts/`;
- the evaluation fixtures and commands: `backend/eval/fixtures/`, `docs/EVALUATION.md`.

The full coding-assistant conversation is not reproduced verbatim; it is not part of the
runtime and would contain workspace paths. No credentials or personal data are included in
this repository. `.env` is ignored; `.env.example` documents the variables.

## Verification trail

| Check | Result |
|---|---|
| `PYTHONPATH=. python -m pytest` | 61 passed |
| `node node_modules/typescript/bin/tsc --noEmit` | clean |
| `npm run build` | app built |
| `npm run build-storybook` | Storybook built |
| Live plan turn | 4 days, sourced itinerary, trace written |
| Live ask turn | answered from plan, no agents |
| Live modify turn | version 2, hotel changed, stops preserved |
| Playwright | no console or page errors on the live flow |

Screenshots: `docs/screenshots/`.
