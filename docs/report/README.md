# Final report

`Pathfinder_Final_Report.docx` is the editable deliverable. `build_report.py` regenerates it from the figures in `assets/` and the recorded results under `docs/eval-artifacts/v5-model-study/`.

## Rebuild

```bash
python docs/report/build_report.py
```

## Evidence used

- `docs/eval-artifacts/v5-model-study/routing-20261123-075040.json` and `routing-20261123-075208.json` — the two current 84-case routing runs.
- `docs/eval-artifacts/v5-model-study/verifier-current.json` — the current 20-case DeepSeek verifier run.
- `docs/eval-artifacts/v5-model-study/RESULTS.md` — frozen planner/editor/ask/memory component study.
- `backend/tests/` — 83 offline tests, including flight windows, turn timeout, PII and the two acceptance regressions.
- `docs/architecture/pathfinder-v5-one-turn.png` — the one-turn architecture diagram used as Figure A1.

The report keeps references and the appendix outside the ten-page main-text target.
