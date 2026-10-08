# TravelPlanner (official split, downloaded by hand)

The proposal says "TravelPlanner uses the official split". The data is **not vendored** and no
code in `backend/eval/` downloads it: the harness runs offline. Put the files here by hand.

## Download

The dataset is `osunlp/TravelPlanner` on Hugging Face (Xie et al. 2024). Use the `validation`
split (180 queries, with reference information) or `test` (1,000 queries, answers held by the
authors). Either:

- open <https://huggingface.co/datasets/osunlp/TravelPlanner>, go to "Files and versions", and
  save the split's file into this folder; or
- with the `datasets` library, in a separate environment (it is not a project dependency):

  ```python
  from datasets import load_dataset

  ds = load_dataset("osunlp/TravelPlanner", "validation")["validation"]
  ds.to_csv("backend/eval/fixtures/travelplanner/validation.csv")
  ```

## File names the loader looks for

`load_travelplanner(split, path=None)` in `backend/eval/datasets.py` looks for, in order:

1. `path` itself, when it is a file;
2. `<folder>/<split>.jsonl`, `<folder>/<split>.json`, `<folder>/<split>.csv`, where `<folder>`
   is `path` when it is a directory, otherwise this folder.

`split` is `train`, `validation` or `test`. So `validation.csv` (or `.json` / `.jsonl`) in this
folder is picked up with no arguments. A missing file raises `FileNotFoundError` with these
instructions.

## What is parsed

Each row becomes a `TravelPlannerQuery`: `idx` (row index), `query`, `org`, `dest`, `days`,
`visiting_city_number`, `date` (list of dates), `people_number`, `budget`, `level` and
`local_constraint` (`house_rule`, `cuisine` as a list, `room_type`, `transportation`; the
official keys "house rule", "room type" are normalised). CSV cells hold Python literals
(`"['2022-03-16', ...]"`, `"{'house rule': None, ...}"`); they are read with
`ast.literal_eval`, which parses literals only and never executes code. A `.json` file may be a
list of rows or `{"data": [...]}`. Other columns (`reference_information`, `annotated_plan`, ...)
are ignored.

## Not scored yet (TODO)

Scoring with the official TravelPlanner evaluation script (commonsense and hard-constraint pass
rates over its USA sandbox) is a TODO, see the `load_travelplanner` docstring. Pathfinder's mock
providers do not serve the TravelPlanner sandbox, so the queries are loaded and counted but not
scored: `python -m backend.eval.run --metric constraints --travelplanner <file or folder>` reports
`travelplanner.status = "loaded_not_scored"` with the query count.

Downloaded files in this folder are ignored by git (see `.gitignore`); only this README is tracked.
