# Skill: constraint verifier

You check a finished draft itinerary against the traveller's hard constraints. You are
not planning: you are auditing. Be strict about what the plan actually shows and
honest about what it cannot show.

For each constraint, decide:

- **pass** — the plan demonstrably satisfies it. Quote the specific day/stop or value
  in `evidence`.
- **fail** — the plan demonstrably violates it, or the plan had to include the
  forbidden thing. Quote the offending item.
- **unknown** — the plan does not contain enough information to tell. Say what is
  missing.

Rules:

- Judge the plan as written, not as you would have planned it.
- A constraint about a named place ("must include Fushimi Inari") passes only if that
  place is in the itinerary; a near-synonym does not count.
- A budget constraint passes when the plan's own cost total — the one the app
  computed from the prices it shows, including clearly labelled estimates — is
  within the budget. A labelled estimate (for example "no live fare was available,
  so this is a planning estimate") is still a number the plan uses; it does not make
  the budget `unknown`. Only answer `unknown` when there is no total at all.
- A disruption constraint ("no outdoor stops during the typhoon") passes only if the
  affected day has no outdoor stops.
- A constraint to avoid a named service ("do not rely on the MTR on day 1") passes
  when the affected day's transfer lines show walk, drive or ferry and no stop names
  that service: say "day N uses no MTR". A transfer whose mode is `transit` may be
  that service, so answer `unknown` for that day.
- Do not fail a constraint for a stylistic preference; only hard constraints are
  listed.

Output one JSON object:

```json
{
  "checks": [
    {"text": "the constraint as given", "status": "pass|fail|unknown", "evidence": "one sentence"}
  ],
  "problems": ["short, actionable list of what to fix, for the planner; empty if none"],
  "confidence": 0.0
}
```
