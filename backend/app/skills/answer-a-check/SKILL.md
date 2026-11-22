---
name: answer-a-check
description: Use when the traveller answers the question about a failed check, including "ok fix".
---

# Answer a failed check

The question is already on the plan. A short reply means do the fix, not start a new edit.

Run the script. It does not change the plan. The application does, using what the script returns.

```bash
python scripts/answer.py "About this question on the plan: I could not meet “flight arrival” ...

ok fix"
```

The script prints JSON: `fix`, `check`, and `relax`. `fix` is true for "ok fix", "yes", "do it", and "relax it". `relax` is true only when they said relax. Otherwise the plan is moved around the check, and a check that the days already satisfy is cleared.
