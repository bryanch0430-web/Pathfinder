# Current verifier benchmark

Model: **deepseek-flash** · endpoint: `https://api.deepseek.com/v1` · temperature 0.0 · cap 1500

| Correct | False passes | False alarms | p50 |
|---:|---:|---:|---:|
| 18/20 | 2 | 0 | 1958.2 ms |

| Case | Gold | Predicted | Correct | Latency |
|---|---|---|---:|---:|
| v01 | pass | pass | yes | 1533 ms |
| v02 | fail | fail | yes | 1758 ms |
| v03 | pass | pass | yes | 1219 ms |
| v04 | fail | fail | yes | 3017 ms |
| v05 | pass | pass | yes | 1718 ms |
| v06 | fail | pass | no | 2000 ms |
| v07 | pass | pass | yes | 2428 ms |
| v08 | fail | fail | yes | 1932 ms |
| v09 | pass | pass | yes | 1411 ms |
| v10 | fail | fail | yes | 1588 ms |
| v11 | pass | pass | yes | 4648 ms |
| v12 | fail | fail | yes | 2166 ms |
| v13 | pass | pass | yes | 1943 ms |
| v14 | fail | pass | no | 2109 ms |
| v15 | pass | pass | yes | 1120 ms |
| v16 | fail | fail | yes | 3231 ms |
| v17 | pass | pass | yes | 2042 ms |
| v18 | fail | fail | yes | 2352 ms |
| v19 | pass | pass | yes | 1524 ms |
| v20 | fail | fail | yes | 1973 ms |
