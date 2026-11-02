# Frozen split

The dev/held-out split is encoded on each fixture (`"split": "dev" | "heldout"`) rather
than in a separate index. `EvalService.seed_defaults()` records the item count and freezes
every shipped dataset on first start. A frozen dataset is refused for scoring if it is
later marked `frozen = false`, and the API has no endpoint that mutates a frozen fixture.

The starter sets are intentionally small. When a set grows:

1. add the new cases with an explicit split;
2. bump the dataset version;
3. seed it as a new dataset id or update the version before the first scored run;
4. never tune prompts on held-out items.

This directory is the place for a future generated `split-<seed>.json` containing the seed
and per-fixture hashes. The current sets do not need it because the split is in the source
fixtures and the freeze is in SQLite.
