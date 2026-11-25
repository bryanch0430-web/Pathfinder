# Pathfinder against a single model call

The 17 plan cases answered twice by one call to the same model (Claude Sonnet 5.5, temperature 0.2), in two arms:
the request alone (`chat_only`), and the request with the same candidate pool, weather and disruption notes that
Pathfinder's composer receives (`single_call`). The single calls were scored with Pathfinder's own validator, verifier
and plan checks, and none of its repairs were applied.

| Arm (34 plans) | Passed | Plans with validator problems |
|---|---|---|
| Pathfinder (plan-suite runs 1 and 2, see `../plan-suites/`) | 30 | 2 |
| One call with the evidence (`single_call`) | 27 | 11 |
| One call with the request only (`chat_only`) | 6 | 14 |

Paired on the first run, Pathfinder passed 15 and the single call 14 (McNemar p = 1.0): the evidence drives the pass
rate, while the orchestration removes infeasible plans and unsourced stops.

File: `baseline_single_llm-20261124-162649.json`. Scores: `../summary.json` → `baseline`, `baseline_paired`.
Report: Table 3, Section 5.2; supplement Figure S4.
