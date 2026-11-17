# Evaluation report — final-report-25170d3

- Dataset: `efficiency` v1.0
- Run: `run_761caf4f25a2`
- Created: 2026-11-16T12:34:09.106961+00:00
- Model snapshot: `{"writer": "sandbox-metered (scripted replies; tokens estimated at 4 characters per token)", "router": "rules first; the model router (scripted offline from the expected intent) only when the rules are unsure", "judge": "deterministic-scripts", "judge_check": {"judge_enabled": false, "judge_model": "deepseek-chat", "judge_family": "deepseek", "writer_model": "grok-4.20-0309-non-reasoning", "writer_family": "xai", "same_family": false, "warning": ""}, "settings": {"router_threshold": 0.8, "preplan_budget_seconds": 90.0, "max_repairs": 2}, "providers": "sandbox"}`

## Metrics

| Metric | Value | Unit | Baseline | Delta |
|---|---:|---|---:|---:|
| API calls saved by the router | 69.6 | % | — | — |
| Tokens saved by the router | 10.2 | % | — | — |
| Turns routed by the rules alone (no router model call) | 90.0 | % | — | — |
| Router path · tool calls / turn | 2.5 | calls | — | — |
| Router path · LLM calls / turn | 1.0 | calls | — | — |
| Router path · tokens / turn | 1313.8 | tokens | — | — |
| Router path · mean latency | 148.3 | ms | — | — |
| Full pipeline · tool calls / turn | 10.5 | calls | — | — |
| Full pipeline · LLM calls / turn | 1.0 | calls | — | — |
| Full pipeline · tokens / turn | 1463.2 | tokens | — | — |
| Full pipeline · mean latency | 226.8 | ms | — | — |
| Tool calls logged (130/130) | 100.0 | % | — | — |

## Cases

| Case | Split | Pass | Failure | Summary |
|---|---|---:|---|---|
| ef01 | dev | yes | — | Router → plan: 10 tool + 1 LLM calls, 1276 tokens, 357 ms. Full pipeline: 10 tool + 1 LLM calls, 1276 tokens, 278 ms. |
| ef02 | dev | yes | — | Router → ask: 0 tool + 1 LLM calls, 1239 tokens, 88 ms. Full pipeline: 10 tool + 1 LLM calls, 1275 tokens, 262 ms. |
| ef03 | dev | yes | — | Router → ask: 0 tool + 0 LLM calls, 0 tokens, 71 ms. Full pipeline: 12 tool + 1 LLM calls, 1708 tokens, 216 ms. |
| ef04 | dev | yes | — | Router → ask: 1 tool + 1 LLM calls, 1365 tokens, 83 ms. Full pipeline: 10 tool + 1 LLM calls, 1275 tokens, 182 ms. |
| ef05 | dev | yes | — | Router → modify: 2 tool + 1 LLM calls, 1404 tokens, 143 ms. Full pipeline: 10 tool + 1 LLM calls, 1275 tokens, 179 ms. |
| ef06 | dev | yes | — | Router → modify: 0 tool + 1 LLM calls, 1396 tokens, 92 ms. Full pipeline: 10 tool + 1 LLM calls, 1275 tokens, 193 ms. |
| ef07 | dev | yes | — | Router → clarify: 0 tool + 2 LLM calls, 1349 tokens, 72 ms. Full pipeline: 11 tool + 1 LLM calls, 1731 tokens, 300 ms. |
| ef08 | heldout | yes | — | Router → plan: 10 tool + 1 LLM calls, 1685 tokens, 244 ms. Full pipeline: 10 tool + 1 LLM calls, 1685 tokens, 257 ms. |
| ef09 | heldout | yes | — | Router → ask: 0 tool + 1 LLM calls, 1996 tokens, 215 ms. Full pipeline: 11 tool + 1 LLM calls, 1731 tokens, 206 ms. |
| ef10 | heldout | yes | — | Router → modify: 2 tool + 1 LLM calls, 1428 tokens, 118 ms. Full pipeline: 11 tool + 1 LLM calls, 1401 tokens, 195 ms. |

**10/10 cases passed.** 0 failures.