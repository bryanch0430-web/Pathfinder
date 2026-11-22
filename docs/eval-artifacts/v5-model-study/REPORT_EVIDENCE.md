# Pathfinder v5 — report evidence pack

Use this with `docs/eval-artifacts/v5-model-study/`. It gives the report-ready version of the
design, the part-model decisions and the task traces that connect claims to code.

## 1. What the system is (one paragraph)

Pathfinder v5 is an LLM-based agentic trip planner. One turn is a bounded agent loop: a context
builder assembles the request, trip state and the available Skill; an LLM router chooses exactly one
path (`plan`, `modify`, `ask` or `clarify`); a runtime harness applies route permissions and budget
limits; parallel tool workers gather evidence from travel providers; a planner may only select
sourced candidate IDs; an evaluator audits the plan and can return it for a bounded repair; the
versioned plan and a trace are saved. Human-in-the-loop clarification, edit proposals and the Desk
queue are part of the same loop. In COMP7608B terms this is a **task-oriented, tool-using planning
agent under a bounded runtime harness** — not an autonomous multi-agent swarm, and not a vector-RAG
system.

## 2. Workflow and autonomous decisions

| Step | What is decided | Code | Observable evidence |
|---|---|---|---|
| Context | What the model sees: request, plan outline, recent turns, Skill, candidate evidence | `app/turn/orchestrator.py`, `app/core/prompts.py` | `trace` event; prompt/generation rows |
| Route | One of plan / modify / ask / clarify; confidence | `app/understand/pipeline.py:understand`; optional `app/understand/jev.py` | intent + confidence in eval rows |
| Gate | Whether confidence is high enough; otherwise clarify/escalate | `settings.router_threshold` | `Router_Gate` sheet |
| Plan path | Which sourced candidates become days/items; budget and transfers | `app/plan/agents.py`, `app/plan/builder.py` | plan JSON, constraints, grounding |
| Edit path | Typed ops; locked/confirmed rows are protected | `app/plan/editor.py:propose_edit`, `_apply` | before/after item IDs, version |
| Ask path | Answer from the current plan, sources and gaps | ask path in `app/turn/orchestrator.py` | reply + sources + known gaps |
| Verify | Constraint audit; pass/fail per constraint | `app/plan/validator.py:audit_constraints` | `VerifyResult` checks |
| Repair | At most the configured repairs; stop on deadline/budget | `app/turn/orchestrator.py`, `app/core/llm.py` | repairs, deadline, stop reason |
| Persist | Version increment, change summary, memory write, trace | `app/api/turn.py`, `app/application/memory.py`, `app/core/trace.py` | plan version, trace ID |

Autonomy is deliberately **bounded**:
- the router may choose only four paths;
- the planner may select only candidate refs, never invent a fact;
- the editor applies typed operations and preserves locked rows;
- the harness has a deadline, repair limit, budget guard and seat lock;
- a human can clarify, accept a proposal, or take over at the Desk.

This is the answer to “what autonomous decisions does it make?”: path selection, candidate selection,
edit operation selection, and whether to clarify/hand off — not open-ended tool use.

## 3. Prompts, tools, data and state

- **Prompts / Skills:** `backend/app/skills/*.md` (`understand.md`, `plan.md`, `edit.md`,
  `verify.md`, `ask.md`, `intake.md`, `memory.md`). They are loaded by `skill()` and recorded by
  commit; the study did not edit them between arms.
- **Tools:** `app/evidence/providers.py` adapters for places, stays, weather, flights, Wikipedia,
  videos and optional Bilibili. Each result becomes `Evidence` with `source_refs` and
  `data_status`; missing data becomes a `DataGap`.
- **Data:** live providers in production; the study runs the real pipeline with sandbox evidence
  (`PATHFINDER_SANDBOX=1`) and the offline atlas as the deterministic fallback. No vector database.
- **State:** working state is `TripContext` + `TripPlan` + recent `ChatMessage`s; durable memory is
  the traveller preference profile; `TurnTrace` records generations, tools, spans and the route.
- **Schema:** prompts return typed Pydantic objects (`Understanding`, `EditDraft`, `VerifyResult`,
  `MemoryDraft`, etc.); invalid JSON is repaired or fails explicitly.

## 4. Model/version and part decisions

Tested on 2026-11-23 through the Poe OpenAI-compatible endpoint, sandbox providers, one run per arm:

| Part | Model | Temperature | Effective cap | Reasoning | Threshold |
|---|---|---:|---:|---|---|
| Analyst | Gemini 3.5 Flash-Lite | 0 | 4,096 | none (unsupported) | 0.8 |
| Turn router | LLM router (Flash); Jev optional | 0 | 4,096 | none | 0.8 |
| Planner | Gemini 3.5 Flash-Lite | 0.2 | 12,000 | none | — |
| Editor | Gemini 3.5 Flash-Lite | 0 | 2,000 | none | — |
| Verifier | DeepSeek V4 Flash | 0 | 1,500 | none | — |
| Ask | DeepSeek V4 Flash | 0 | 1,400 | none | — |
| Memory | DeepSeek V4 Flash, with a no-invention guard | 0 | 700 | none | — |

Why, in report language:
- **Routing:** Flash-Lite and Sonnet both scored 83/84 (98.8%, Wilson 93.6–99.8). Flash-Lite took
  4.1 s and 2.9k tokens; Sonnet took 6.3 s and 4.6k. Sonnet 0 vs 0.2 was identical, so 0 is the
  reproducible setting.
- **Jev:** 79/84 (94.0%, 0.6 s). It is perfect on plan but weak on modify/ask; at a 0.8 gate it keeps
  66% at 100% and escalates 29. A derived Jev→Flash fallback scored 84/84 at about 2.0 s; this is a
  proposed next step, not a shipped setting. Do not claim it as the current router.
- **Planner:** Both 14/17 (82.4%). Flash is much faster (323 s vs 920 s) and has better HK
  constraints (85.7% vs 68.8%); Sonnet is 3/3 on TravelPlanner vs Flash 2/3. Small n; state that
  the planner result is the least certain.
- **Editor:** All three 8/8; Flash 3.4 s, Sonnet 5.6 s, DeepSeek 6.6 s. Flash is fastest; DeepSeek
  uses fewer tokens but is slower on this set.
- **Verifier:** DeepSeek and Sonnet 18/20 (2 false passes); Flash 17/20 (3 false passes). All missed
  transfer arithmetic — keep deterministic checks.
- **Ask:** DeepSeek 11/11 in-scope (16.1 s), Sonnet 11/11 (20.2 s), Flash 10/11 (14.4 s).
- **Memory:** DeepSeek recall 91.7% vs 79.8% for both others, but it invented preferences on the
  no-fact case. Use a write-time guard or choose Sonnet for precision.

**Reasoning effort:** not tested. The client only sends `reasoning_effort` to GPT-5/o-series; those
models failed on the available Poe key with `400 upstream gateway failure`. Report this as a provider
limitation, not as evidence that reasoning does not help. Re-run with a direct OpenAI key if the
report needs a reasoning arm.

## 5. Trade-offs to state

1. **One writer vs per-part models.** A single Sonnet writer is operationally simpler and is the
   current default. Per-part routing/planning with Flash and editing/verification with DeepSeek
   improves measured score and token cost, but adds provider and prompt-version surface area.
2. **Quality vs latency.** Jev is ~10× faster than the LLM router but 4.8 points less accurate.
   The hybrid recovers accuracy but adds a second call path.
3. **Autonomy vs control.** The planner cannot invent a place; this protects grounding but can
   leave a visible gap where a more autonomous agent would guess.
4. **Cap vs completeness.** The planner cap is 12,000 because the day-by-day JSON is long; lowering
   it truncates. The other parts fit comfortably below their caps.
5. **Verifier vs arithmetic.** An LLM verifier is useful for semantic constraints, but all models
   missed a transfer-arithmetic case; code checks remain authoritative.

## 6. Task traces to cite

- Routing: `docs/eval-artifacts/v5-model-study/routing-merged.json`, cases with `trace_id`
  `bench-<arm>-<case>`.
- Planner: `datasets-latest.json`; eval DB `backend/data/bench_<dataset>_<arm>.db`; each row has the
  case ID and pass/fail/constraint/grounding scores.
- Editor: `editor-direct-latest.json`; trace IDs `editor-<arm>-<case>`.
- Verifier: `verifier-latest.json`; trace IDs `verify-<arm>-<case>`.
- Ask: `ask-adjudicated.json`; per-question reply and missing/forbidden checks.
- Memory: `memory-latest.json`; extracted preferences vs gold.
- Caps: `cap-stress-latest.json`; low vs effective cap.
- Code map: `TRACE_INDEX.md`.

## 7. Suggested report sentences

> We evaluated each part separately rather than treating the writer as one undifferentiated model.
> The routing set (84 labelled utterances) showed Gemini 3.5 Flash-Lite matching Claude Sonnet 5.5
> at 98.8% (83/84) while cutting median latency from 6.3 s to 4.1 s and tokens from 4.6k to 2.9k.
> On the 17 planning cases both passed 14 (82.4%); Flash-Lite was much faster (323 s vs 920 s) and
> met 85.7% of Hong Kong constraints against 68.8%, while Sonnet was 3/3 on TravelPlanner-style cases
> against Flash's 2/3. Flash-Lite was fastest on the 8-case editor set (all 8/8); DeepSeek matched
> Sonnet on the 20-case verifier (18/20) with lower latency. These are screening results on small
> sets; we report intervals and limitations rather than significance claims.

> The router is deliberately bounded. Jev was faster (0.6 s) but only 94.0% accurate; it is a
> candidate pre-gate, not the default. The default remains the LLM router, with a 0.8 confidence
> gate. A derived Jev→Flash fallback reached 84/84 on this set but is future work.

> All verifier arms missed the 10-minute-transfer-vs-30-minute constraint, so we keep deterministic
> arithmetic and transfer checks alongside the LLM evaluator.
