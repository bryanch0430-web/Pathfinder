# ChatAnywhere auto-test harness

This is a **test-only** provider configuration. It is not a production default and the real key is stored only in the ignored `.env.autotest`.

## Setup

```bash
cp .env.autotest.example .env.autotest
# fill AUTOTEST_API_KEY and, if available, JEV_API_KEY
```

The committed key is never in the repository; `.env.autotest` and `.env.autotest.*` are ignored.

## Run

```bash
cd backend
PATHFINDER_SANDBOX=1 PYTHONPATH=. ./.venv/Scripts/python.exe scripts/autotest_llm.py --mode probe
```

- `--mode probe` checks JSON/schema compatibility for any model id.
- `--mode understand` runs the real `understand` skill and `ParsedIntent` schema.
- `--models` accepts any comma-separated ids, with no allow-list.
- `--all` fetches the endpoint catalog and excludes non-chat modalities.
- `--jev` calls Jev only when `JEV_API_KEY` is set; it never hardcodes the key.

Example:

```bash
python scripts/autotest_llm.py \
  --models claude-sonnet-5-5,gemini-3.5-flash-lite,deepseek-v4.1-flash,gpt-5.5 \
  --mode probe --jev
```

## Current result

`autotest-latest.json` / `.md` records the latest probe. It contains model ids, latency, token counts and errors only; it does not contain the API key.
