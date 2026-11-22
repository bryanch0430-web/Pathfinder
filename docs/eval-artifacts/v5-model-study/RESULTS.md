# v5 part-model study — results

Generated: 2026-11-23T02:57:32 (Asia/Hong_Kong)

## Executive summary

| Part | Chosen | Temp | Cap | Reasoning | Threshold |
|---|---|---:|---:|---|---|
| Analyst / understanding | Gemini 3.5 Flash-Lite | 0 | 4,096 | none (not supported by these Poe models) | 0.8 (0.9 when false routes are costlier) |
| Turn router | LLM router (Flash-Lite) default; Jev as optional fast pre-gate | 0 | 4,096 | none | 0.8 for the LLM; 0.8 for a Jev pre-gate |
| Planner | Gemini 3.5 Flash-Lite | 0.2 (production setting; not swept) | 12,000 | none | — |
| Editor | Gemini 3.5 Flash-Lite | 0 | 2,000 | none | — |
| Verifier | DeepSeek V4 Flash | 0 | 1,500 | none | — |
| Ask / quick answers | DeepSeek V4 Flash | 0 | 1,400 | none | — |
| Memory | DeepSeek V4 Flash with a no-invention check; Sonnet if precision is the priority | 0 | 700 | none | — |

The full protocol and case catalogue are in `TEST_PLAN.md`; the spreadsheet is `v5_model_study.xlsx` (formatted) and `v5_model_study.xls` (compatibility).

## 1. Analyst / router

| Arm | Model | Temp | Correct | Accuracy | Wilson 95% | p50 ms | Mean tokens |
|---|---|---:|---:|---:|---|---:|---:|
| sonnet-0.0 | claude-sonnet-5.5 | 0.0 | 83/84 | 98.8% | 93.6%–99.8% | 6302.8 | 4634.3 |
| sonnet-0.2 | claude-sonnet-5.5 | 0.2 | 83/84 | 98.8% | 93.6%–99.8% | 6212.6 | 4657.1 |
| flash-lite-0.0 | gemini-3.5-flash-lite | 0.0 | 83/84 | 98.8% | 93.6%–99.8% | 4108.2 | 2942.7 |
| jev | jev-latest | None | 79/84 | 94.0% | 86.8%–97.4% | 624.6 | None |

### Confidence gate

| Arm | cutoff | coverage | accuracy on kept | escalated |
|---|---:|---:|---:|---:|
| sonnet-0.0 | 0.6 | 99% | 98.8% | 1 |
| sonnet-0.0 | 0.7 | 96% | 98.8% | 3 |
| sonnet-0.0 | 0.8 | 96% | 98.8% | 3 |
| sonnet-0.0 | 0.9 | 49% | 100.0% | 43 |
| sonnet-0.2 | 0.6 | 100% | 98.8% | 0 |
| sonnet-0.2 | 0.7 | 98% | 98.8% | 2 |
| sonnet-0.2 | 0.8 | 96% | 98.8% | 3 |
| sonnet-0.2 | 0.9 | 51% | 100.0% | 41 |
| flash-lite-0.0 | 0.6 | 90% | 98.7% | 8 |
| flash-lite-0.0 | 0.7 | 90% | 98.7% | 8 |
| flash-lite-0.0 | 0.8 | 90% | 98.7% | 8 |
| flash-lite-0.0 | 0.9 | 89% | 100.0% | 9 |
| jev | 0.6 | 89% | 96.0% | 9 |
| jev | 0.7 | 75% | 98.4% | 21 |
| jev | 0.8 | 66% | 100.0% | 29 |
| jev | 0.9 | 43% | 100.0% | 48 |

### Jev pre-gate + LLM fallback

| Hybrid | Correct | Accuracy | Jev coverage | LLM fallbacks | Mean latency (sequential) |
|---|---:|---:|---:|---:|---:|
| jev+sonnet@0.8 | 83/84 | 98.8% | 66% | 29 | 3329 ms |
| jev+flash@0.8 | 84/84 | 100.0% | 66% | 29 | 1998 ms |

The hybrid is sequential: Jev answers when confidence ≥ 0.8, otherwise the analyst is called. This is an option analysis, not a shipped setting.

## 2. Planner

| Dataset | Arm | Model | Passed | Constraints | Grounding | Latency (s) |
|---|---|---|---:|---:|---:|---:|
| hongkong | sonnet | claude-sonnet-5.5 | 5/8 | 68.8 | 100.0 | 416.9 |
| hongkong | flash | gemini-3.5-flash-lite | 6/8 | 85.7 | 100.0 | 175.8 |
| japan | sonnet | claude-sonnet-5.5 | 6/6 | 100.0 | 100.0 | 213.4 |
| japan | flash | gemini-3.5-flash-lite | 6/6 | 100.0 | 100.0 | 73.2 |
| travelplanner | sonnet | claude-sonnet-5.5 | 3/3 | 100.0 | — | 290.2 |
| travelplanner | flash | gemini-3.5-flash-lite | 2/3 | 83.3 | — | 74.4 |

Sonnet total 14/17 (82.4%); Flash total 14/17 (82.4%). Flash is much faster; Sonnet is 3/3 on TravelPlanner while Flash is 2/3.

## 3. Editor

| Arm | Model | Correct | p50 ms | Mean tokens |
|---|---|---:|---:|---:|
| sonnet | claude-sonnet-5.5 | 8/8 | 5631.1 | 11550.1 |
| flash | gemini-3.5-flash-lite | 8/8 | 3415.7 | 3362.9 |
| deepseek | deepseek-v4-flash | 8/8 | 6630.9 | 3081 |

The editor test uses four frozen plans (remove, move, time change, remove) and the same prompts for each arm. It does not include add/research edits.

## 4. Verifier

| Arm | Model | Correct | False pass | False alarm | p50 ms |
|---|---|---:|---:|---:|---:|
| sonnet | claude-sonnet-5.5 | 18/20 | 2 | 0 | 5653.4 |
| flash | gemini-3.5-flash-lite | 17/20 | 3 | 0 | 3392.6 |
| deepseek | deepseek-v4-flash | 18/20 | 2 | 0 | 4004.0 |

All arms missed v06 (a 10-minute transfer against a 30-minute constraint). The validator now receives source lines (`plan_text(with_sources=True)`) so source constraints can be audited.

## 5. Ask / quick answers

| Arm | Model | In-scope correct | Accuracy | p50 ms |
|---|---|---:|---:|---:|
| sonnet | claude-sonnet-5.5 | 11/11 | 100.0% | 20151.8 |
| flash | gemini-3.5-flash-lite | 10/11 | 90.9% | 14418.8 |
| deepseek | deepseek-v4-flash | 11/11 | 100.0% | 16126.9 |

q11 (capital of France) is a scope case: all three clarified instead of answering. It is excluded from the in-scope denominator and reported as a product scope limitation.

## 6. Memory

| Arm | Model | Mean recall | No-fact cases with invented prefs | p50 ms |
|---|---|---:|---:|---:|
| sonnet | claude-sonnet-5.5 | 79.8% | 0 | 3824.1 |
| flash | gemini-3.5-flash-lite | 79.8% | 0 | 3388.7 |
| deepseek | deepseek-v4-flash | 91.7% | 1 | 5356.6 |

## 7. Cap stress

| Part | Low cap | Low result | Effective cap | Effective result |
|---|---:|---|---:|---|
| analyst | 64 | PASS — unclear | 4096 | PASS — plan |
| ask | 64 | FAIL — I need one detail before I can plan. | 1400 | FAIL — I need one detail before I can plan. |
| verifier | 64 | FAIL — unknown | 1500 | PASS — pass |
| memory | 64 | FAIL —  | 700 | PASS — I love visiting temples and gardens.; I prefer a slow pace with a long lunch.; I dislike early starts. |
| planner | 128 | FAIL — 0 days | 12000 | FAIL — 0 days |

The ask and planner low-cap controls were inconclusive because the control turn itself clarified/failed; they are reported, not used to claim causality. Verifier and memory show the expected direction.

## 8. Failures and limitations

- One run per arm; temperature > 0 was not repeated except the router Sonnet 0.2 arm.
- GPT-5/o-series could not run on the available Poe endpoint (400 upstream gateway failure); Gemini 3.8 Flash returned an empty smoke answer. DeepSeek's full routing arm timed out under concurrency 4 and is excluded from routing; it ran in editor/verifier/ask/memory.
- Editor n=8, verifier n=20, ask n=11 in-scope, memory n=8. These are exploratory, not powered for 5-point claims.
- Reasoning effort is not sent to Claude/Gemini/DeepSeek by the client; no reasoning arm is possible on this provider.
- Planner temperature was not swept; 0.2 is the production setting.
- Ask latency is high (15–20 s) for all models; model choice does not fix the ask path's latency.
- The verifier's transfer-arithmetic weakness remains; code checks are still required.
- The Jev hybrid is a derived result from saved per-case predictions, not a live end-to-end run.

## 9. Trace / code map

See `TRACE_INDEX.md` for the claim → code path table and sample trace IDs.
