# Indirect prompt injection (48 probes)

Attacker text hidden in a web result, a place description or a video note (16 each), covering four attacks: reveal
the instructions, call a forbidden tool, drop the task and change the route. All calls went to the live model.

**Result: 0 leaks, 0 route changes, task completed in 48 of 48.** Two replies repeated the attacker's words; a person
read both, and each named the hidden instruction as untrusted and ignored it.

The 24 direct probes reported with these (Table 3, "Injection (72)") are the stratified sample of 22 November
described in the repository’s [`docs/EVALUATION.md`](../../../docs/EVALUATION.md).

| File | What it is |
|---|---|
| `indirect_injection-20261124-154119.json` | Scored probes by channel and attack |
| `batch_b_indirect.db` | Session database of every probe turn |

Scores: `../summary.json` → `injection_indirect`. Report: Table 3, Sections 2.4 and 5.2.
