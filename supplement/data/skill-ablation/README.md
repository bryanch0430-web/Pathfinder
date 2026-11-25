# Skills against a minimal prompt

Each part ran with its Markdown skill file and with a minimal prompt that keeps only the role and the output contract,
on the same cases, model (Claude Sonnet 5.5) and harness.

| Part (cases) | With skill | Minimal prompt | Note |
|---|---|---|---|
| Analyst (84 routing labels) | 83 | 71 | 12 right only with the skill, none only without (McNemar p = 0.0005) |
| Planner (17 plan cases) | 15 | 16 | p = 1.0; validator problems in 2 against 4 final plans |
| Editor (8 edits) | 8 | 8 | slower and about 31,000 against 21,000 tokens per edit without the skill |
| Verifier (17 altered plans) | 15 | 15 | the same two missed clock checks |

| File | What it is |
|---|---|
| `skills_ablation-20261124-155819.json` | Scored cases of all four parts (recorded on commit `f6552fa`) |
| `skills_planner_full.db`, `skills_planner_minimal.db` | Planner turns in each condition |

Scripts: `../harness/batch_d.py` and `../harness/repo-scripts/v5_skills_ablation.py`. Scores: `../summary.json` →
`skills`. Report: Table 3, Sections 2.4 and 5.2; supplement Figure S5.
