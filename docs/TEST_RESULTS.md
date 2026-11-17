# Test results

Run on 2026-11-17 against `main` @ `3e4d01b` (clean tree, Windows 11).

| Suite | Command | Result |
|---|---|---|
| Backend (pytest) | `cd backend && .venv/Scripts/python.exe -m pytest -q` | 3992 passed, 1 skipped, 0 failed, 0 errors (about 4 min 53 s) |
| Frontend types | `cd frontend && npx tsc --noEmit` | exit 0, no type errors |

Notes:
- The counts come from the per-test dots in the pytest output (the summary line is
  suppressed by the project's pytest config).
- The only warning is a Starlette deprecation about `httpx` in `TestClient`; it is harmless.
- The earlier 5→16 Nov failures (20 failing tests) are explained in
  [EVAL_REGRESSIONS.md](EVAL_REGRESSIONS.md); none remain.
- Not run here: live-LLM evals (see [EVALUATION.md](EVALUATION.md)) and browser tests.
