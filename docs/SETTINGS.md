# Settings audit (v5)

The Settings screen is the v4 screen. This table says what each control actually
does in v5, so no one tunes a knob that is not connected.

| Section | Control | Live in v5? | What it changes |
|---|---|---|---|
| Model keys | provider / key / model, test, choose | **yes** | the writer for every turn (BYOK overrides the env writer) |
| Writer | model, temperature, max tokens | **yes** | the writer call parameters (stored in `/api/admin/runtime`) |
| Writer | reasoning | no (passed but the writer endpoint may ignore it) | — |
| Thresholds | router confidence | **yes** | the analyst confidence gate (`ROUTER_THRESHOLD` default) |
| Thresholds | max repairs | **yes** | compose attempts on a validation problem |
| Thresholds | turn deadline | **yes** | per-turn SSE deadline (seconds) |
| Thresholds | memory similarity, preplan budget, rule passes | no | v4 rule-engine knobs |
| Privacy | HKID / phone / email / card + patterns | **yes** | first-layer masking of the traveller's text before storage and any model call |
| SerpAPI rotation | keys, status, add | **yes** | the key used for live search and flights (admin keys first, then `SERPAPI_KEY_POOL`) |
| Freshness hours | weather / tickets / hotels | no | v5 fetches per turn; no cache to expire |
| Orchestrator | gate (Jev / plain / reason) | no | v5 has one writer; the analyst is the router |
| Orchestrator | Jev URL / key / model | no | Jev is not wired (see the Jev note in the report) |
| Reviewer | model, rounds, reasoning | no | no second-model review call is made |
| Model arms / `models` | plain / reason / reviewer names | no | one writer model per turn |
| Escalation | enabled, frustration, stuck, examples | **yes** | handoff to the desk: asking for a person, frustration ≥ threshold, or a stuck streak |
| Redaction preview | sample text | **yes** | preview only, nothing stored |
| Admin runtime reset | — | **yes** | drops the overrides above |

`/api/runtime` now reports a `capabilities` object with the same booleans, so a
future UI can disable the inert controls instead of leaving them looking live.

## Why some v4 controls are not wired

- **Jev**: a second, less accurate router in front of the analyst (92.9 % vs 98.8 %
  on the same 84 cases). It remains a deliberate pre-gate option, not a control.
- **Reviewer / arms / gate**: v5 uses one writer for every role; the v4 arms were
  model-comparison experiments. Their code paths do not exist, so the controls are
  shown for layout parity only.
- **Freshness hours**: there is no cache; each turn fetches what it needs and records
  the fetch time on the evidence.
