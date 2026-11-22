# Version notes

Running notes for **Pathfinder v5**. The UI is still the v4 React app; the core is
the rebuilt backend at `backend/`.

## 5.0.0 — 2026-11-23

The LLM-first revamp is the first release. One writer model (BYOK-capable) answers
every role by default; a typed analyst call decides the path; evidence is gathered
from live providers or the offline atlas; the composer can only choose sourced
references; the plan is validated, repaired at most twice, and stored as a version.

### Models and routing

- **Models tab** (renamed from Writer): the default writer/key card, **per-part
  model**, **temperature**, and **reasoning effort** for analyst, planner, editor,
  verifier, ask and memory. Blank means the default writer model.
- Per-part models and tuning share the active provider key, including BYOK. Separate
  keys/providers per part are not supported yet.
- Reasoning effort is sent as `reasoning_effort` only to OpenAI reasoning models
  (GPT-5 / o-series); other providers store it but do not use that field.
- **Turn orchestrator** can be the **Analyst LLM** (default) or **Jev** (TypeSafe
  system-one). Jev chooses plan/modify/ask/clarify; the analyst still extracts slots
  and constraints, and the analyst route is kept if Jev is unreachable.
- Writer temperature/max-tokens now persist (`writer_call` was previously posted but
  ignored by the backend).

### Grounding and evidence

- Named-place lookup: Google Places live, Wikipedia offline, acronym matching
  (`HKU`), city/disambiguation guards, and web-search fallback for described wishes
  (“a place with Gundam”).
- Atlas entries for Hong Kong Disneyland, HKU, Tian Tan Buddha, Ghibli Museum,
  Unicorn Gundam, and vegan venues in Tokyo/Osaka.
- Flight evidence now carries outbound and return times; plans start day 1 after
  landing and end the last day before the return flight.
- **Live providers are the default** (`PATHFINDER_SANDBOX=0`). The curated atlas is
  an operator option under **Settings → General → Data source**, and remains the
  fallback when a live provider fails.
- Every stop and stay gets a photo. The image endpoint uses city-aware Wikipedia
  search, then Google Places, then a neutral generated tile instead of a broken
  image.

### Privacy and memory

- **PII first layer**: HKID, HK phone, email and Luhn-valid cards are masked before
  storage and before any model call. Stale patterns that cannot match a known sample
  fall back to the built-in pattern instead of silently disabling masking.
- **Memory** is extracted by the model on finalise / useful rating (durable
  preferences only) and injected into later turns; memory extraction is a background
  task so rating stays instant.

### Conversation and handoff

- Clarify chips always carry a sendable message; saved cards survive a refresh; a
  click made while the turn is closing waits for idle instead of doing nothing.
- Assistant replies render safe Markdown (bold, italic, code, links, bullet and
  numbered lists); user text stays literal.
- Handoff has a **master** switch (off by default), an optional frustration threshold,
  an optional stuck counter (failed edits only — ordinary clarifications no longer
  count), and an editable **explicit-request phrase list** (default: “live agent”,
  “real person”, “human agent”, “talk to a person”, 转人工, 真人客服).
- A handoff goes to the **queue with no agent assigned**; a person presses **Accept**
  in the Desk to take it. Phrase requests work even when automatic handoff is off.

### Interface fixes

- Desk tiles show destination and trip dates instead of the raw UUID, with compact
  ages and ellipsis.
- Finalise is a full-size button; feedback is optimistic and the memory call no
  longer delays a star click.
- Settings tabs are now General · Handoff · Instructions · Models · Thresholds ·
  Redaction · System. Jev, reviewer and freshness controls that nothing read were
  removed from the UI and from `/api/runtime`.

### Known limits

- Per-part **separate keys/providers** are not implemented (models/tuning only).
- `reasoning_effort` is effective only on OpenAI reasoning models.
- Jev is optional and off by default; it is a router, not a replacement for the
  analyst.
- The full Hong Kong evaluation’s last recorded run was **7/8**; the transfer-realism
  changes for `hk03` are in the code but the full suite was not re-run after them.
