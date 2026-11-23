# Settings audit (v5)

The Settings screen keeps the existing look but only shows controls the v5 runtime reads.
If a control is not listed here, it is not in the UI.

| Section | Control | Live in v5? | What it changes |
|---|---|---|---|
| General | Developer mode | **yes** | shows the per-turn decision trace |
| General | Data source (Live providers / Curated place corpus) | **yes** | `data_mode` (`live` or `corpus`) in `/api/admin/runtime`; live uses Google Places, Open-Meteo, SerpAPI, Wikipedia |
| Handoff | Explicit request phrases (on/off) | **yes** | message contains a phrase → queue; also covers the analyst's own “wants a person” detection; negations ignored |
| Handoff | Automatic handoff (on/off) | **yes** | frustration-only handoff; independent of the other two switches |
| Handoff | Frustration threshold (0–1) | **yes** | analyst frustration ≥ threshold, while automatic handoff is on |
| Handoff | Stuck chats (on/off) + failed-turn count (2–8) | **yes** | independent of automatic handoff; counts edits that leave the trip unchanged |
| Handoff | Handoff message | **yes** | stored with the policy and shown by the Desk |
| Instructions | Skill overrides | **yes** | per-skill prompt override (stored in kv, falls back to `app/skills/*.md`) |
| Models | Provider / key / default model / temperature | **yes** | active writer and BYOK key; default model. The default reply cap is no longer shown here. |
| Models | Per-part model | **yes** | `purpose_models`; blank = default writer |
| Models | Per-part temperature | **yes** | `purpose_temperatures`, 0–2 |
| Models | Per-part reasoning effort | **yes** | `purpose_reasoning`; sent as `reasoning_effort` only to GPT-5/o-series |
| Models | Per-part reply cap | **yes** | `purpose_max_tokens`, 16–32,000. The field shows the effective cap (planner 12,000; editor 2,000; verifier 1,500; ask 1,400; memory 700; understand 4,096). |
| Models | Constraint verifier endpoint / key | env only | `VERIFIER_BASE_URL`, `VERIFIER_API_KEY`, `VERIFIER_MODEL`; defaults to DeepSeek `deepseek-flash` at temperature 0, cap 1,500. Separate from the writer key. |
| Models | Constraint verifier model override | **yes** | `purpose_models.verify` or the **Models by part** field; blank uses `VERIFIER_MODEL`. |
| Models | Turn orchestrator (Analyst / Jev) | **yes** | Jev routes plan/modify/ask/clarify before the analyst; fallback to analyst |
| Models | Jev endpoint / model / key | **yes** | used only when Jev is the orchestrator; key is stored server-side and shown masked |
| Thresholds | LLM router confidence (`ANALYST_CONFIDENCE_THRESHOLD`) | **yes** | Low-confidence edit clarification; model-reported, not deterministic |
| Thresholds | Jev confidence (`JEV_CONFIDENCE_THRESHOLD`) | **yes** | Separate Jev acceptance threshold; only when Jev routes |
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
