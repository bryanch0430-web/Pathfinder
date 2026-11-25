# Typed edit against a full replan (5 paired changes)

Each of five change requests was run on the same seeded trip as a typed edit and as a fresh plan request with the
change stated. The proposed plan was scored, because an edit is stored as a proposal until the traveller accepts it.

| | Edit | Replan |
|---|---|---|
| Median turn time | 11.3 s | 72.5 s |
| Mean tokens | 18,748 | 35,972 |
| Mean model calls | 1.8 | 4.8 |
| Judged (change made **and** unnamed stops kept) | 2 of 5 | 0 of 5 |

The judged row is read case by case from `per_case`. Two summary fields need care: `change_applied` (2 against 4)
counts only whether the change landed and ignores collateral changes to other stops, and `kept_share_mean` (0.4) mixes
stops that were kept with cases that asked a question and changed nothing.

| File | What it is |
|---|---|
| `paired_edit_replan-20261124-160045.json` | Both arms per case; the edit arm re-read from the proposal (`../harness/rerun_edit_arm.py`) |
| `batch_b_paired.db` | The seeded trips and every turn of both arms |

Scores: `../summary.json` → `edit_vs_replan`. Report: Table 4, Sections 2.4 and 5.2; supplement Figure S6.
The first reading of this run is in `../excluded/`.
