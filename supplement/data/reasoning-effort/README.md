# Reasoning effort on the routing prompt

GPT-5.5 on the 84 frozen routing labels with the submitted routing prompt, reasoning effort none, low and medium.

| Reasoning effort | Correct of 84 | Median | 95th percentile |
|---|---|---|---|
| none | 84 | 3.3 s | 5.4 s |
| low | 83 | 4.8 s | 8.6 s |
| medium | 83 | 7.4 s | 18.1 s |

Extra reasoning added time without accuracy, which is why every part of the submitted build runs with reasoning effort
none. File: `reasoning_ablation-20261124-160808.json`. Scores: `../summary.json` → `reasoning`. Report: Section 5.2;
supplement Table S10.
