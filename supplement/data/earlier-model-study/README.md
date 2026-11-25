# Earlier model study (22–23 November 2026)

A copy of `docs/eval-artifacts/` from the submitted code. `v5-model-study/` holds the part-by-part model comparison
(routing arms, planner, editor, verifier, answers, memory, temperature, output caps, pricing and the routing
confidence); `autotest/` holds the repository's own suite runs of that week.

How to read it:

- `v5-model-study/RESULTS.md` and `PART_MODEL_DECISIONS.md` record the recommendation of 23 November (Gemini 3.5
  Flash-Lite for the analyst, planner and editor). That is not what shipped; the report explains why Claude Sonnet 5.5
  was kept.
- The report's confidence figures are for the shipped Sonnet arm, computed from `v5-model-study/routing-merged.json`;
  the AUROC and calibration error printed in `RESULTS.md` belong to the Flash-Lite arm.
- The hybrid Jev-then-analyst router in `RESULTS.md` was an option that was not shipped.
- The `autotest/` files that record no model tokens are the runs removed by the validity screen
  (see [`../excluded/README.md`](../excluded/README.md)).
