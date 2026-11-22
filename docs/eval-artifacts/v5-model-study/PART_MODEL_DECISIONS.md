# v5 part-model decisions

This is the report-ready decision record. Evidence tables are in `RESULTS.md`; the full protocol is in `TEST_PLAN.md`.

## Analyst / understanding

- **Chosen:** Gemini 3.5 Flash-Lite, temperature 0, cap 4,096, no reasoning, threshold 0.8 (0.9 if a wrong route is more expensive than an escalation).
- **Evidence:** 83/84 = 98.8% (Wilson 93.6–99.8), p50 4.1 s, 2.9k tokens. Sonnet 5.5 was also 83/84 but 6.3 s and 4.6k tokens. Sonnet 0 vs 0.2 was identical (83/84), so temperature 0 is the reproducible choice.
- **Why not Sonnet:** no accuracy gain on this set; slower and more tokens.
- **Why not Jev alone:** 79/84 = 94.0%; it is excellent on plan (21/21) but weaker on modify/ask (both 100% for the LLM), and its 0.8 gate keeps only 66%.
- **Rejected:** Gemini 3.8 Flash (empty answer in smoke), GPT-5 nano/mini (Poe 400).
- **Code:** `app/understand/pipeline.py:understand`, `app/core/llm.py:model_for/temperature_for/max_tokens_for`.

## Turn router

- **Chosen:** LLM router (Flash-Lite) as the default. Keep Jev as an optional latency path.
- **Evidence:** LLM 98.8% at 4.1 s; Jev 94.0% at 0.6 s. At a 0.8 gate, Jev keeps 66% at 100% and escalates 29 cases. A **derived** Jev→Flash fallback scored 84/84 (100%, Wilson 95.7–100) at a mean sequential latency of about 2.0 s; Jev→Sonnet scored 83/84 at about 3.3 s.
- **Trade-off:** the hybrid is the best measured option, but it adds a second call path and is not a shipped setting; it is a recommendation for the next version. Pure Jev is the fastest but not the most accurate.
- **Threshold:** product 0.8 is a reasonable default; Flash at 0.9 kept 89% at 100%, so 0.9 is the quality-first variant.
- **Code:** `app/understand/jev.py:jev_route`, `app/understand/pipeline.py`, `settings.router_threshold`.

## Planner

- **Chosen:** Gemini 3.5 Flash-Lite, temperature 0.2, cap 12,000, no reasoning.
- **Evidence:** 12/14 = 85.7% vs Sonnet 11/14 = 78.6%; Hong Kong constraints 85.7% vs 68.8%; Japan 6/6 for both. HK 176 s vs 417 s; Japan 73 s vs 213 s.
- **Trade-off:** n=14, one run; Sonnet had a stronger prior full run, so the planner choice is the least certain. The 12,000 cap is deliberate: planner JSON exceeded a 6,000 cap earlier.
- **Why not lower cap:** the plan JSON is the longest output; the cap must cover the day-by-day JSON, not prose.
- **Code:** `app/plan/builder.py:build_plan`, `app/plan/agents.py`, `app/core/llm.py`.

## Editor

- **Chosen:** DeepSeek V4 Flash, temperature 0, cap 2,000.
- **Evidence:** 4/4, p50 5.3 s, 3.1k mean tokens; Sonnet 4/4, 6.7 s, 20.3k; Flash 3/4, 3.8 s.
- **Trade-off:** n=4; DeepSeek matches Sonnet at much lower token cost and is faster; Flash missed the move edit. This test covers remove, move, time and remove, not add/research edits.
- **Why not Flash:** the miss was a real semantic failure, not a scoring artefact.
- **Code:** `app/plan/editor.py:propose_edit`, `_apply`.

## Verifier

- **Chosen:** DeepSeek V4 Flash, temperature 0, cap 1,500.
- **Evidence:** 9/10, 1 false pass, p50 4.8 s; Sonnet 9/10, 1 false pass, 5.0 s; Flash 8/10, 2 false passes, 3.4 s.
- **Trade-off:** all arms missed v06 (10-minute transfer against a 30-minute constraint). Model choice cannot fix arithmetic/temporal audit alone; deterministic checks must stay.
- **Why not Flash:** two false passes vs one; verifier errors are asymmetric (a false pass is worse).
- **Code:** `app/plan/validator.py:audit_constraints`; `plan_text(with_sources=True)` now exposes source lines.

## Ask / quick answers

- **Chosen:** DeepSeek V4 Flash, temperature 0, cap 1,400.
- **Evidence:** 11/11 in-scope (100%), p50 16.1 s; Sonnet 11/11, 20.2 s; Flash 10/11, 14.4 s.
- **Trade-off:** all three are slow (15–20 s) because the ask path re-reads the plan; model choice does not fix that. The off-topic q11 was clarified by all three and is reported as a scope issue, not counted in accuracy.
- **Why not Sonnet:** same in-scope accuracy, lower latency for DeepSeek.
- **Code:** ask path in `app/turn/orchestrator.py`; prompts in `app/skills/`.

## Memory

- **Chosen:** DeepSeek V4 Flash with a write-time no-invention check; Sonnet 5.5 if precision is the priority.
- **Evidence:** DeepSeek recall 91.7% vs 79.8% for Sonnet and Flash; but DeepSeek produced preferences for m06, the “nothing to remember” case. Sonnet/Flash produced none.
- **Trade-off:** best recall vs one false memory in eight. Memory write must validate each preference against the message/plan; otherwise choose Sonnet.
- **Code:** `app/application/memory.py:derive_memory`, `remember_trip`.

## Temperature, caps, reasoning

- **Temperature:** 0 for routing, editor, verifier, ask and memory (reproducible structured output). Planner keeps 0.2 as the production setting; it was not swept, so this is a limitation, not a proven optimum.
- **Caps:** 4,096 analyst; 12,000 planner; 2,000 editor; 1,500 verifier; 1,400 ask; 700 memory. These are effective defaults, not arbitrary: plan JSON is the only large output; the verifier/memory low-cap controls failed as expected. Ask/planner low-cap controls were inconclusive and are not claimed as causal.
- **Reasoning:** none on these Poe models. The client only sends `reasoning_effort` to GPT-5/o-series; those models failed on the available endpoint, so no reasoning arm exists. This is a provider limitation, not evidence that reasoning does not help. Revisit with a direct OpenAI key.
- **Threshold:** 0.8 default; 0.9 for Flash when avoiding a confident wrong route is more important than coverage.

## Overall recommendation

For a **quality-first** profile: Jev pre-gate at 0.8 + Flash-Lite fallback for routing; Flash-Lite planner; DeepSeek editor/verifier/ask; DeepSeek memory with validation.
For a **simpler** profile: Flash-Lite for analyst/planner; Sonnet for editor/verifier/ask/memory (the current one-writer default), accepting the higher token cost.
For **latency-first**: Jev router alone, accepting 94% routing and 66% coverage at 0.8.
