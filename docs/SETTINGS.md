# Settings audit (v5)

The Settings screen keeps the v4 look but only shows controls the v5 runtime reads.
If a control is not listed here, it is not in the UI.

| Section | Control | Live in v5? | What it changes |
|---|---|---|---|
| General | Developer mode | **yes** | shows the per-turn decision trace |
| General | Data source (Live providers / Offline atlas) | **yes** | `data_mode` in `/api/admin/runtime`; live uses Google Places, Open-Meteo, SerpAPI, Wikipedia |
| Handoff | Automatic handoff (master) | **yes** | when off, no frustration/stuck handoff; an explicit request still queues |
| Handoff | Frustration threshold (0–1) | **yes** | analyst frustration ≥ threshold, only while the master is on |
| Handoff | Stuck chats + failed-turn count (2–8) | **yes** | counts edits that leave the trip unchanged; only while the master is on |
| Handoff | Explicit request phrases | **yes** | message contains a phrase → queue, even when automatic is off (negations ignored) |
| Handoff | Handoff message | **yes** | stored with the policy and shown by the Desk |
| Instructions | Skill overrides | **yes** | per-skill prompt override (stored in kv, falls back to `app/skills/*.md`) |
| Models | Provider / key / default model / temperature / max tokens | **yes** | active writer and BYOK key; default model |
| Models | Per-part model | **yes** | `purpose_models`; blank = default writer |
| Models | Per-part temperature | **yes** | `purpose_temperatures`, 0–2 |
| Models | Per-part reasoning effort | **yes** | `purpose_reasoning`; sent as `reasoning_effort` only to GPT-5/o-series |
| Models | Turn orchestrator (Analyst / Jev) | **yes** | Jev routes plan/modify/ask/clarify before the analyst; fallback to analyst |
| Models | Jev endpoint / model / key | **yes** | used only when Jev is the orchestrator; key is stored server-side and shown masked |
| Thresholds | Router confidence | **yes** | analyst confidence gate |
| Thresholds | Max repairs | **yes** | compose attempts on a validation problem |
| Thresholds | Turn deadline | **yes** | per-turn deadline in seconds |
| Privacy | HKID / phone / email / card + patterns | **yes** | first-layer masking before storage and model calls; stale patterns fall back to defaults |
| System | Providers, SerpAPI keys, injection counts | **yes** | SerpAPI rotation uses the active admin key first |
| Admin runtime reset | — | **yes** | drops all runtime overrides |

`/api/runtime` exposes `capabilities` (`writer`, `byok_keys`, `thresholds`,
`privacy_masking`, `serpapi_rotation`, `jev`, `purpose_models`) so a future UI can
keep inert controls disabled.

## Removed from the UI

- **Orchestrator / gate / model arms** — v5 has one writer and the analyst routes;
  Jev is now an explicit orchestrator option, not a hidden gate.
- **Reviewer** — there is no second-model review call.
- **Updates / freshness windows** — there is no cache; each turn records fetch time
  on its evidence.
