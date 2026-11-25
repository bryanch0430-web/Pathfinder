# Plan suites, three runs on the submitted build

The 8 Hong Kong disruption cases, the 6 Japan constraint cases and the 3 TravelPlanner-style cases, each run three
times on the sandbox corpus with live sources blocked, so that only the model calls varied. A case passes when every
hard constraint passes (unknown counts as a failure), at least 99% of its stops are sourced and the injected
disruption is handled.

| Run | Hong Kong | Japan | TravelPlanner-style |
|---|---|---|---|
| 1 | 7/8 | 5/6 | 3/3 |
| 2 | 7/8 | 5/6 | 3/3 |
| 3 | 8/8 | 6/6 | 3/3 |

Every stop in all 51 plans was sourced. The Hong Kong miss in runs 1 and 2 is the MTR-delay case, where the broad
check "keep travel times realistic" objected to two straight-line transfer estimates in sandbox mode.

| File | What it is |
|---|---|
| `hk_r1.db` … `hk_r3.db`, `jp_tp_r1.db` … `jp_tp_r3.db` | Session databases of each run (plans, messages, traces; `eval_runs` holds the scored run) |
| `*.log` | Console output; the first line records `max_repairs 2` |
| `*.db.blocked.json` | Live hosts the sandbox refused (a positive count means the turn stayed on sandbox data) |
| `batch_a_console.log` | Console of the whole batch |

Scores: `../summary.json` → `plans`. Report: Table 3, Section 5.2; supplement Figure S3.
