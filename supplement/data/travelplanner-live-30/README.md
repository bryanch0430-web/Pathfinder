# TravelPlanner 30-case evaluation

This folder is the 24 November 2026 run of 30 public TravelPlanner test queries through Pathfinder. Use it for the final report. It is not the official leaderboard score.

## Files

| File | What it is |
|---|---|
| `tp30-report.md` | The checking report. Headline numbers and the 30-row table. |
| `tp30-results.json` | Raw per-case checks, tool status, constraint pass/fail/unknown, and plan totals. |
| `run_tp30.py` | The runner and the scorer. |
| `runs/TP-*.db` | One saved session per case (place-photo keys replaced with `REDACTED_GOOGLE_API_KEY`). |
| (not included) | The benchmark splits `test.csv`, `train.csv` and `validation.csv` are the public [osunlp/TravelPlanner](https://huggingface.co/datasets/osunlp/TravelPlanner) dataset (CC BY 4.0). |

## What was run

- Code: Pathfinder `main` at `017e3bf` (24 November 2026). This is an earlier build than the submitted tag `submission-v5.2.11`: the application code changed after it, so treat this run as a diagnostic of that build.
- Writer: Claude Sonnet 5.5 through Poe. Verifier: DeepSeek Flash. Sandbox off, so places, hotels, flights, weather, and videos were live.
- Clock given to the router: 1 March 2022, so a March 2022 trip is not treated as the past. Live flight and weather providers still have no March 2022 data, so those two workers come back partial and the flight fare is a labelled estimate.
- Sample: Hugging Face `osunlp/TravelPlanner`, test split, seed `20261124`. Ten easy, ten medium, ten hard. Inside each level: 4 one-city, 3 two-city, 3 three-city.
- The user message is the dataset `query`. The row's origin, destination, dates, party, budget, and local constraints are loaded before that message. Three cases asked a clarifying question and were answered once with those same row values.

## Headline result

**0 / 30** pass every check at once.

On the 29 plans that were saved:

| Check | Result |
|---|---|
| Plan saved | 29 / 30 (`TP-E06` timed out at 300s) |
| Days, start date, party match | 29 / 29 |
| Every stop cites a tool result from this run | 29 / 29 |
| One-city plan is in the reference city | 12 / 12 |
| Multi-city plan visits every reference city | 0 / 17 |
| Named stops also in `reference_information` | 81 / 151 one-city (54%), 4 / 136 multi-city (3%) |
| Plan total within the stated budget | 8 / 29 |
| App constraint checker all-pass | 3 / 29 |

Verifier marks across those 29 plans: 77 checks, 25 pass, 31 fail, 21 unknown. Budget is the main fail (8 pass, 21 fail). House rules and room type are mostly unknown, because the live hotel tool does not return the dataset's house-rule or room-type fields.

## Do not call this the official TravelPlanner score

The benchmark's own checker is `evaluation/eval.py` in [OSU-NLP-Group/TravelPlanner](https://github.com/OSU-NLP-Group/TravelPlanner) (Xie et al., ICML 2024, [arXiv:2402.01622](https://arxiv.org/abs/2402.01622)). A submission is JSONL in dataset order, 180 validation rows or 1,000 test rows, with `"plan": []` for anything unfinished. Final pass rate uses that full denominator.

Official rates are:

- Delivery rate: a plan was produced.
- Commonsense constraint micro / macro: environment and plan-sanity checks (closed loop, meals, attractions, accommodation, within the sandbox catalogue, and so on). Micro is the fraction of individual checks. Macro is the fraction of plans that pass every commonsense check.
- Hard constraint micro / macro: the query's budget, cuisine, room type, house rule, and transport rules.
- Final pass rate: plans that pass every commonsense check and every hard check, divided by the full split.

This 30-case run did not use that script and did not use the 2022 sandbox database. It scored live tool grounding, reference-city coverage, budget, and the constraints Pathfinder's own verifier recorded. Cite it as a 30-case diagnostic on the test split, not as a leaderboard number.
