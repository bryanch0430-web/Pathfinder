# Evaluation report — final-report-25170d3

- Dataset: `japan` v1.0
- Run: `run_1bdd2bb36655`
- Created: 2026-11-16T12:33:59.037574+00:00
- Model snapshot: `{"writer": "sandbox-offline", "router": "rules", "judge": "deterministic-scripts", "judge_check": {"judge_enabled": false, "judge_model": "deepseek-chat", "judge_family": "deepseek", "writer_model": "grok-4.20-0309-non-reasoning", "writer_family": "xai", "same_family": false, "warning": ""}, "settings": {"router_threshold": 0.8, "preplan_budget_seconds": 90.0, "max_repairs": 2}, "japan_replay": "offline replay of provider-shaped responses (eval/fixtures/japan_replay.json)"}`

## Metrics

| Metric | Value | Unit | Baseline | Delta |
|---|---:|---|---:|---:|
| Constraint satisfaction (met/total) | 100.0 | % | — | — |
| Constraints met (of 25) | 25.0 |  | — | — |
| Must-include constraints met | 100.0 | % | — | — |
| Dietary constraints met | 100.0 | % | — | — |
| Grounding (tool log) | 100.0 | % | — | — |
| Edit preservation | 100.0 | % | — | — |
| Transfer times grounded (22/22) | 100.0 | % | — | — |
| Opening hours grounded (33/33) | 100.0 | % | — | — |
| Stops with hours unknown (of 41) | 8.0 |  | — | — |
| Replay: rows parsed as the source sent them | 100.0 | % | — | — |
| Replay: one record per real place | 100.0 | % | — | — |
| Replay: coordinate conflicts settled | 100.0 | % | — | — |
| Replay: partial failures recorded | 100.0 | % | — | — |
| Held-out share | 33.3 | % | — | — |
| Split frozen | 100.0 | % | — | — |
| Mean latency | 119.3 | ms | — | — |
| Tool calls logged (83/83) | 100.0 | % | — | — |

## Cases

| Case | Split | Pass | Failure | Summary |
|---|---|---:|---|---|
| jp01 | dev | yes | — | A 4-day trip to Kyoto for 2 travellers. |
| jp02 | dev | yes | — | A 5-day trip to Tokyo for 3 travellers. |
| jp03 | heldout | yes | — | A 3-day trip to Osaka for 2 travellers. |
| jp04 | dev | yes | — | A 2-day trip to Tokyo for 2 travellers. |
| jp05 | dev | yes | — | A 2-day trip to Osaka for 1 traveller. |
| jp06 | heldout | yes | — | A 3-day trip to Kyoto for 4 travellers. |

**6/6 cases passed.** 0 failures.

## Plan checks

| Case | Constraints | Disruption | Grounding | Transfers | Opening hours |
|---|---:|---|---:|---:|---:|
| jp01 | 4/4 | — | 16/16 | 1/1 | 4/4 (1 unknown) |
| jp02 | 3/3 | — | 34/34 | 6/6 | 10/10 (1 unknown) |
| jp03 | 4/4 | — | 24/24 | 5/5 | 6/6 (2 unknown) |
| jp04 | 5/5 | — | 19/19 | 4/4 | 5/5 (1 unknown) |
| jp05 | 5/5 | — | 18/18 | 4/4 | 4/4 (2 unknown) |
| jp06 | 4/4 | — | 16/16 | 2/2 | 4/4 (1 unknown) |