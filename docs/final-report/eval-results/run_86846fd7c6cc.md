# Evaluation report — final-report-25170d3

- Dataset: `hongkong` v1.0
- Run: `run_86846fd7c6cc`
- Created: 2026-11-16T12:33:55.655483+00:00
- Model snapshot: `{"writer": "sandbox-offline", "router": "rules", "judge": "deterministic-scripts", "judge_check": {"judge_enabled": false, "judge_model": "deepseek-chat", "judge_family": "deepseek", "writer_model": "grok-4.20-0309-non-reasoning", "writer_family": "xai", "same_family": false, "warning": ""}, "settings": {"router_threshold": 0.8, "preplan_budget_seconds": 90.0, "max_repairs": 2}}`

## Metrics

| Metric | Value | Unit | Baseline | Delta |
|---|---:|---|---:|---:|
| Constraint satisfaction (met/total) | 100.0 | % | — | — |
| Constraints met (of 31) | 31.0 |  | — | — |
| Must-include constraints met | 100.0 | % | — | — |
| Exclusion constraints met | 100.0 | % | — | — |
| Weather-alert constraints met | 100.0 | % | — | — |
| Travel-time constraints met | 100.0 | % | — | — |
| Error recovery | 85.7 | % | — | — |
| Typhoon signal handled | 100.0 | % | — | — |
| Venue closure recovered | 50.0 | % | — | — |
| MTR delay handled | 100.0 | % | — | — |
| Source timeout recovered | 100.0 | % | — | — |
| Grounding (tool log) | 100.0 | % | — | — |
| Edit preservation | 100.0 | % | — | — |
| Transfer times grounded (42/42) | 100.0 | % | — | — |
| Opening hours grounded (51/51) | 100.0 | % | — | — |
| Stops with hours unknown (of 60) | 9.0 |  | — | — |
| Held-out share | 25.0 | % | — | — |
| Split frozen | 100.0 | % | — | — |
| Mean latency | 71.5 | ms | — | — |
| Tool calls logged (114/114) | 100.0 | % | — | — |

## Cases

| Case | Split | Pass | Failure | Summary |
|---|---|---:|---|---|
| hk01 | dev | yes | — | A 3-day trip to Hong Kong for 2 travellers. The Hong Kong Observatory has Typhoon Signal No. 8 in force on Thu 17 Dec. M |
| hk02 | heldout | yes | — | A 2-day trip to Hong Kong for 4 travellers. |
| hk03 | heldout | yes | — | A 3-day trip to Hong Kong for 1 traveller. MTR reports a delay on the Tsuen Wan Line on Wed 16 Dec. Each metro transfer  |
| hk04 | dev | yes | — | A 2-day trip to Hong Kong for 2 travellers. |
| hk05 | dev | yes | — | A 2-day trip to Hong Kong for 3 travellers. |
| hk06 | dev | yes | — | A 2-day trip to Hong Kong for 6 travellers. MTR reports a delay on the Island Line on Thu 17 Dec. Each metro transfer th |
| hk07 | dev | no | venue_closure_not_handled | A 2-day trip to Hong Kong for 1 traveller. |
| hk08 | dev | yes | — | A 2-day trip to Hong Kong for 1 traveller. The Hong Kong Observatory has Typhoon Signal No. 8 in force on Wed 16 Dec. Mo |

**7/8 cases passed.** 1 failures.

## Plan checks

| Case | Constraints | Disruption | Grounding | Transfers | Opening hours |
|---|---:|---|---:|---:|---:|
| hk01 | 4/4 | typhoon: handled | 31/31 | 7/7 | 9/9 (1 unknown) |
| hk02 | 4/4 | venue_closure: handled | 29/29 | 8/8 | 8/8 (2 unknown) |
| hk03 | 4/4 | transit_delay: handled | 31/31 | 7/7 | 9/9 (1 unknown) |
| hk04 | 3/3 | — | 19/19 | 4/4 | 5/5 (1 unknown) |
| hk05 | 3/3 | source_timeout: handled | 19/19 | 4/4 | 5/5 (1 unknown) |
| hk06 | 5/5 | transit_delay: handled | 19/19 | 4/4 | 5/5 (1 unknown) |
| hk07 | 4/4 | venue_closure: not handled | 19/19 | 4/4 | 5/5 (1 unknown) |
| hk08 | 4/4 | typhoon: handled | 19/19 | 4/4 | 5/5 (1 unknown) |