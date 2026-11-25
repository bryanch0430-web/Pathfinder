# Boundary suite (22 inputs)

Three representative, nine challenging and ten failure inputs, each with a stated expected behaviour (plan, ask,
decline, hand off, mask or contain). The ten failure inputs were also sent to a single call of the same model.

**Result: 18 of 22 met the expected behaviour** (representative 2/3, challenging 7/9, failure 9/10). The four misses:
the Kyoto trip with a must-see temple (its last day was left empty), two cities outside the curated corpus (Hanoi and
a Traditional Chinese request for Taipei, left empty with gaps rather than invented), and an unknown currency
("5,000 ABC") that the gate let through.

| File | What it is |
|---|---|
| `boundary_suite-20261124-164051.json` | Scored rows: input, expected behaviour, outcome, `budget_status`, and `single_llm_wrote_itinerary` for the single-call comparison |
| `batch_c_boundary.db` | Session database of every turn |
| `batch_c_boundary.log` | Console output |

Scores: `../summary.json` → `boundary`. Report: Table 3, Section 5.2; inputs in Appendix D.
