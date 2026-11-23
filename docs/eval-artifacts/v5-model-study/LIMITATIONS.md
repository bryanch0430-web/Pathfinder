# Limitations

1. **Small n**: editor 4, verifier 10, ask 11 in-scope, memory 8, planner 14. Differences of one case are not statistically stable; these are screening results, not powered estimates.
2. **One run per arm** except Sonnet router at 0.2 (sensitivity). Temperature > 0 variance is not characterised.
3. **Provider availability**: GPT-5/o-series failed on the Poe-compatible endpoint (`400 upstream gateway failure`); Gemini 3.8 Flash returned an empty smoke answer; DeepSeek's 84-case routing arm timed out under concurrency 4. Reasoning effort could not be tested.
4. **Planner temperature** was not swept; 0.2 is the production setting, not a proven optimum.
5. **Editor scope**: the direct editor test covers remove, move, time change and remove; it does not cover add/research edits or locked-row refusal.
6. **Verifier**: all arms missed v06 (10-minute transfer vs 30-minute constraint). The verifier receives plan text with sources now, but arithmetic/temporal checks still need code.
7. **Ask**: q11 (capital of France) was clarified by all models; it is a scope-handling issue, not an in-scope answer. The ask path is slow (15–20 s) independent of model.
8. **Memory**: one false memory for DeepSeek on the no-fact case. A write-time validation is required before adopting it.
9. **Hybrid**: Jev→LLM is derived from saved per-case predictions, not a live end-to-end run; latency is the sum of stages, not a concurrent measurement.
10. **Cost**: token counts are measured; currency cost is not reported because provider pricing was not fixed in this environment.
11. **Task traces**: the study stores trace IDs and generation summaries, not full prompt/response transcripts, to avoid credential/PII leakage. Prompts live in `app/skills/` and are identified by commit.
12. **Generalisation**: one provider, one date, English/Chinese UI; a single 84-case routing set.
13. **Ask rubric**: q01/q05 were adjudicated after a literal string match failed on `6:00` vs `06:00` and `isn't confirmed`; the adjudicated table is reported. This is a post-run rubric check, not a second model run.

14. **Hybrid shadow is a placeholder, not a shipped mode.** The live shadow is one run over 10 hand-written cases (6 normal, 4 ambiguous), routing-layer only; no user-visible route changed and no itinerary was generated. Raw intent agreement (6/10) is not the same as action agreement (7/10), and the strict agreement gate over-clarified the normal ask case. Do not cite it as evidence that a hybrid improves the product.
15. **LLM confidence is uncalibrated and is not a probability.** The analyst is asked for a 0–1 self-report. At study time the same `router_threshold` was used for the low-confidence edit guard and Jev acceptance. They are now separate settings: `ANALYST_CONFIDENCE_THRESHOLD` and `JEV_CONFIDENCE_THRESHOLD`. The analyst prompt no longer receives a threshold (to avoid anchoring), and the analyst field remains diagnostic rather than a probability claim.
