# Runs that are not used in the report

Kept for transparency; none of them feeds a number in the report.

| File | Why it is not used |
|---|---|
| `paired_edit_replan-20261124-155542.json` | First reading of the edit arm. The grader read the saved plan, which does not change until the traveller accepts a proposal, so every stop looked kept. The corrected reading is `../edit-vs-replan/paired_edit_replan-20261124-160045.json`. |
| `failure_only/` | A re-run of the ten failure inputs alone. The reported 22-input result is `../boundary-suite/`. |
| `routing_r1.db`, `routing_r1.log` | One routing pass stored by batch A and never scored. Routing figures come from the earlier model study (`../../../docs/eval-artifacts/v5-model-study/routing-merged.json`). |

Intermediate saves of the skill ablation were deleted; the finished file is
`../skill-ablation/skills_ablation-20261124-155819.json`.

The runs removed by the validity screen (supplement Table S8) are the files in `../../../docs/eval-artifacts/autotest/`
that record no model tokens: `injection-chatanywhere-latest.json` (84 probes), `efficiency-chatanywhere-latest.json`
(10 path-cost messages), `edit_vs_replan-chatanywhere-latest.json` (5 edits) and `travelplanner-fixed-latest.json`
(the TravelPlanner-style re-run). Each returned the fallback reply, so it scored the fallback rather than the system.
