# Testing data

**Date:** 22 November 2026, 16:45 HKT  
**Branch:** `testing-data`  
**This branch holds data only.** There is no application code here. The product is on `main` at v1.18.0 (`44ba4ac`).

## TravelPlanner — 1,225 queries

Official public set, [osunlp/TravelPlanner](https://huggingface.co/datasets/osunlp/TravelPlanner).

| Split | File | Queries |
|---|---|---:|
| train | `backend/data/travelplanner/travelplanner_train.csv` | 45 |
| validation | `backend/data/travelplanner/travelplanner_validation.csv` | 180 |
| test | `backend/data/travelplanner/travelplanner_test.csv` | 1,000 |

Last run: `backend/data/travelplanner/sandbox-report.json`.

| Check | Result |
|---|---:|
| Destination | 1,225 / 1,225 |
| Origin | 1,224 / 1,225 |
| Days | 1,225 / 1,225 |
| Party | 1,225 / 1,225 |
| Budget | 1,225 / 1,225 |
| Mock accepted the call | 1,225 / 1,225 |

The writer was not called. Flight, hotel, and place were mocks. This is not the official TravelPlanner plan score. The one origin miss is a test query labelled Phoenix whose sentence never names a departure city.

`docs/eval-artifacts/travelplanner-sandbox-20261122.md` matches that table at the top. A later section of the same note still says 1,162 calls. Trust the JSON. The `.xlsx` beside it is the spreadsheet from the same day.

## Regional sentences — four regions

| Region | File | Queries |
|---|---|---:|
| Hong Kong | `backend/eval/regional/hongkong.jsonl` | 1,000 |
| Taiwan | `backend/eval/regional/taiwan.jsonl` | 1,000 |
| Japan | `backend/eval/regional/japan.jsonl` | 1,000 |
| China | `backend/eval/regional/china.jsonl` | 1,000 |

Each set scored 1,000 / 1,000 on origin, destination, days, party, budget, and “the wish is not the city”. They are eight sentence shapes with the city and the numbers swapped. A quarter name an animal, a flower, a food, or a vibe.

## Earlier dumps

| File | What it is |
|---|---|
| `docs/eval-artifacts/one-test-summary.json` | 20 November 2026 model grid. Seed `pathfinder-one-test-2026-11-20`. |
| `docs/eval-artifacts/one-test-deepseek-v41-high-summary.json` | DeepSeek V4.1 Flash, high reasoning. |
| `docs/eval-artifacts/one-test-sonnet-planner-temps-summary.json` | Sonnet planner at temperatures 0.1, 0.4, and 1.0. |
| `docs/eval-artifacts/gate-compare-20261117-212025.json` | Router-gate comparison, 17 November 2026. |
