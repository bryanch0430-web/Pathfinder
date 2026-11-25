# Evaluation harness

The scripts that produced the submitted-build runs. They drive the repository's own runner and scripts and do not
modify the product code.

| File | What it does |
|---|---|
| `run_all.ps1` | Runs the batches in order |
| `run_batch_a.ps1` | Batch A: plan suites in three runs; its other passes were not loaded into `../summary.json` and are not used |
| `batch_b.py` | Batch B: indirect injection, the paired edit-against-replan run, reasoning effort |
| `batch_c.py` | Batch C: single-call baseline, boundary suite, TravelPlanner sample |
| `batch_d.py` | Batch D: skill ablation |
| `rerun_edit_arm.py` | Re-reads the edit arm from the proposal rather than the saved plan |
| `offline.py` | Blocks live web, maps and search hosts (answers 503 and counts them); model calls go out |
| `analyze_results.py` | Writes `../summary.json` from the databases and JSON files; calls no model |
| `repo-scripts/v5_skills_ablation.py` | Skill-ablation script, not in the repository; copy it into `backend/scripts/` |

Paths at the top of each script point at the machine that ran them; change `$repo`, `$tests` and `$out` (or `REPO`
and `RESULTS`) before use. `analyze_results.py` reads one flat folder, so copy the files of the suite folders into one
folder first. The shipped settings are set explicitly (`PURPOSE_MODELS`, `MAX_REPAIRS=2`), because an empty override
does not clear a local `.env`.
