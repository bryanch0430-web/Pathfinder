# Live dual-router shadow

Generated: 2026-11-23T13:58:04+08:00

Mode: **live shadow**. The analyst route is the real route; Jev is called in parallel only to log a second opinion. No user-visible decision changes.

- Cases: **10** (6 normal, 4 ambiguous)
- Raw intent agreement: **6/10 = 60.0%**
- Action agreement: **7/10** accepted/clarify decisions
- Would clarify: **7/10**
- Mean parallel wall time: **8649 ms** (analyst 8649 ms, Jev 330 ms)

| id | category | analyst action | Jev action | intent agreement | action agreement | would clarify | outcome |
|---|---|---|---|---:|---:|---:|---|
| normal-taipei | normal | clarify | plan | True | False | yes | would_clarify |
| normal-kyoto | normal | clarify | plan | True | False | yes | would_clarify |
| normal-taipei-dated | normal | plan | plan | True | True | no | accept analyst route (plan) |
| normal-kyoto-dated | normal | plan | plan | True | True | no | accept analyst route (plan) |
| normal-edit | normal | modify | modify | True | True | no | accept analyst route (modify) |
| normal-question | normal | ask | clarify | False | False | yes | would_clarify |
| ambiguous-sakura | ambiguous | clarify | clarify | False | True | yes | would_clarify |
| ambiguous-gundam | ambiguous | clarify | clarify | True | True | yes | would_clarify |
| ambiguous-warm | ambiguous | clarify | clarify | False | True | yes | would_clarify |
| ambiguous-plan | ambiguous | clarify | clarify | False | True | yes | would_clarify |

## Reading the result

- **Normal** cases test whether the routers agree on complete requests.
- **Ambiguous** cases test whether they agree that the request needs clarification; sakura and Gundam are deliberately underspecified.
- `would_clarify` is a **policy simulation**, not a real clarify response; no plan was generated or changed.
- Jev `unavailable` means the call failed or timed out; the analyst run is still valid.
- If the two disagree, the proposed policy would ask a disambiguating question rather than silently pick a route.

## What the live shadow showed

- **Raw intent agreement was 6/10**, but **action agreement was 7/10**. A `plan` with a clarifying question and an `unclear` answer can both mean “clarify”; raw labels understate agreement.
- All four **ambiguous** cases (`sakura`, `Gundam`, `somewhere warm`, `plan a trip`) ended with both routers requiring clarification. They agreed on the *action*, even when they disagreed on the label.
- The two normal requests without a start date were interpreted as `plan` by both routers, but the analyst correctly needed the date and Jev did not. The action gate still clarified.
- The normal **ask** case was the failure mode: the analyst said `ask`, Jev said `clarify`, so a strict disagreement gate would turn a valid question into a clarification.
- Dated normal plans and the edit were accepted by both.
- Jev took about **330 ms**; the analyst about **8.6 s**. Running them in parallel added no observed wall-time because the analyst dominated.

**Conclusion:** do not ship a strict agreement gate yet. It over-clarified the ask case and did not improve the already-clarify cases. The next policy should treat the analyst's missing-slot/clarify decision as primary, use Jev as a logged second opinion, and only consider a hybrid gate after repeated live shadow runs. This is a small, single-run sample, not a significance test.
