# Frozen split

The proposal (TC-06, p4) asks for the custom evaluation sets to be split 70/30 into dev and
held-out, stratified by city and constraint type, and frozen before the first scored run.
`app/dataset_splits.py` does this.

## How the split is made

- Each fixture row still carries its own `"split": "dev" | "heldout"`.
- `python -m app.dataset_splits freeze <dataset>...` assigns the split and writes it onto
  the fixture rows. It also writes `split-20261009.json`, the manifest in this directory.
- The held-out total is 30% of the set, rounded. Each stratum gets its proportional share,
  and leftover places go to the strata with the largest remainder. Within a stratum, the
  held-out items come first in an order hashed from the seed (`20261009`). The assignment
  is repeatable, and adding items to a set moves as few existing items as possible.
- What a stratum is depends on the set:
  - routing: expected intent × destination;
  - injection: attack × channel;
  - Hong Kong and Japan: destination × constraint type (the fault, else "hard
    constraints", else "baseline").
- For each set the manifest records the seed, the item count, the dev and held-out counts
  per stratum, and a SHA-256 of the parsed rows. The hash ignores line endings and
  whitespace.

## Frozen sets

| Dataset | Items | Dev | Held-out |
|---|---:|---:|---:|
| `routing` | 84 | 59 | 25 |
| `injection` | 84 | 59 | 25 |
| `hongkong` | 8 | 6 | 2 |
| `japan` | 6 | 4 | 2 |

`travelplanner`, `efficiency` and `edit_vs_replan` have a `split` on every row but are not
in the manifest.

## Checking the freeze

```bash
cd backend
PYTHONPATH=. python -m app.dataset_splits check     # prints "frozen: hongkong, injection, japan, routing"
```

`check` exits with 1 and names the problem if a frozen fixture has been edited, if an item
has moved between splits, or if a named set is missing from the manifest. The routing and
injection trial reports show the same thing as "Held-out share" and "Split frozen" cards.
`tests/test_dataset_splits.py` runs the check and the 70/30 test for routing and
injection. Hong Kong and Japan are checked only by the command so far.

There is a second, older guard in SQLite. `EvalService.seed_defaults()` marks every
shipped dataset `frozen` when it is first seeded, and a scored run against a dataset whose
flag has been turned off is refused with `409`.

## Changing a set

1. Add the new cases to the fixture.
2. Run `python -m app.dataset_splits freeze <dataset>` before the first scored run, and
   commit the fixture and the manifest together.
3. Never tune prompts on held-out items.
