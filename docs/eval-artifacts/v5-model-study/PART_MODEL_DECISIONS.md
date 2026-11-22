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
- **Evidence:** 14/17 each (82.4%). Hong Kong constraints 85.7% vs 68.8%; Japan 6/6 both; TravelPlanner 2/3 vs 3/3. Total 323 s vs 920 s.
- **Trade-off:** n=17, one run. Flash ties overall and is much faster; Sonnet is stronger on TravelPlanner-style constraints. The 12,000 cap is deliberate: planner JSON exceeded a 6,000 cap earlier.
- **Why not lower cap:** the plan JSON is the longest output; the cap must cover the day-by-day JSON, not prose.
- **Code:** `app/plan/builder.py:build_plan`, `app/plan/agents.py`, `app/core/llm.py`.

## Editor

- **Chosen:** Gemini 3.5 Flash-Lite, temperature 0, cap 2,000.
- **Evidence:** 8/8 for all three; p50 3.4 s (Flash) vs 5.6 s (Sonnet) and 6.6 s (DeepSeek); mean tokens 3.4k vs 11.6k (Sonnet) and 3.1k (DeepSeek).
- **Trade-off:** All three pass the eight remove/move/time cases; Flash wins the latency tie-break, while DeepSeek uses slightly fewer tokens but is slower. This test does not cover add/research edits.
- **Why not DeepSeek:** same accuracy, slower; no quality reason to pay the latency.
- **Code:** `app/plan/editor.py:propose_edit`, `_apply`.

## Verifier

- **Chosen:** DeepSeek V4 Flash, temperature 0, cap 1,500.
- **Evidence:** 18/20, 2 false passes, p50 4.0 s; Sonnet 18/20, 2 false passes, 5.7 s; Flash 17/20, 3 false passes, 3.4 s.
- **Trade-off:** all arms missed transfer arithmetic (v06/v14). Model choice cannot fix arithmetic/temporal audit alone; deterministic checks must stay.
- **Why not Flash:** three false passes vs two; verifier errors are asymmetric (a false pass is worse).
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

## Uniform Sonnet 5.5 at temperature 0.2 as the baseline

The results **can** justify keeping Sonnet 5.5 at 0.2 for every part as a **conservative,
single-provider baseline**, but not as the empirically optimal configuration:

| Part | Sonnet 5.5 | Best measured | Sonnet status |
|---|---|---|---|
| Analyst | 98.8% (83/84), 6.3 s | 98.8% (Flash, 4.1 s) | tied on accuracy; slower |
| Planner | 14/17, 920 s total | 14/17 (Flash, 323 s) | tied; stronger on TravelPlanner (3/3 vs 2/3) |
| Editor | 8/8, 5.6 s, 11.6k tokens | 8/8 (Flash, 3.4 s, 3.4k) | tied on accuracy; slower/more tokens |
| Verifier | 18/20, 5.7 s | 18/20 (DeepSeek, 4.0 s) | tied; slower |
| Ask | 11/11 in-scope, 20.2 s | 11/11 (DeepSeek, 16.1 s) | tied; slower |
| Memory | 79.8% recall, no false memory | 91.7% (DeepSeek, one false memory) | lower recall; better precision |
| Turn router | 98.8% | 98.8% (Flash, same) | tied; Jev alone is 94.0% |

So Sonnet is at the best measured accuracy on six of seven parts (all except memory recall),
which supports it as a **quality floor and lowest-integration-risk choice**. It is not the
latency or token-cost winner, and the report must say so.

**Temperature caveat:** the study measured Sonnet at 0.2 directly for routing (identical to
0.0: 83/84) and for planning (the planner arms inherited 0.2). Editor, verifier, ask and memory
were measured at **0.0**, not 0.2. A claim that 0.2 is equally good for those four parts is an
extrapolation, not a result. If the report wants that claim, run the 0.2 arms on those parts or
state it as the production default rather than a tested optimum.

**How to phrase it:** “We retain Sonnet 5.5 at 0.2 as the uniform baseline because it ties the
best measured accuracy on six of seven parts, uses one provider/key, and avoids per-part model
integration risk. The study shows Flash/DeepSeek are faster or cheaper; those are documented
alternatives, not the baseline.”
