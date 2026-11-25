# TravelPlanner sample (12 queries, sandbox)

Twelve easy queries from the TravelPlanner benchmark in their own wording, with the clock fixed at 1 March 2022 so that
the dates are not in the past, on the sandbox corpus.

**Result:** 12 plans, every requested day count matched. The six queries about New York and San Francisco, which are in
the corpus, produced fully sourced plans (median 11.5 stops); the six about other cities stayed empty with the gaps
shown, without any invented place.

| File | What it is |
|---|---|
| `travelplanner_sample-20261124-163221.json` | Scored queries |
| `batch_c_travelplanner.db` | Session database of every turn |
| `batch_c_tp.log` | Console output |

Scores: `../summary.json` → `travelplanner`. Report: Table 3; supplement Section S5.2.
