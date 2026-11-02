#!/usr/bin/env bash
# Pathfinder 30 development helpers.
#   ./scripts/dev.sh backend|frontend|storybook|test|eval [dataset] [tag]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASK="${1:-backend}"
DATASET="${2:-hongkong}"
TAG="${3:-baseline-v1}"

case "$TASK" in
  backend)
    cd "$ROOT"
    PYTHONPATH=backend python -m uvicorn app.main:app --port 8100 --host 127.0.0.1
    ;;
  frontend)
    cd "$ROOT/frontend" && npm run dev
    ;;
  storybook)
    cd "$ROOT/frontend" && npm run storybook
    ;;
  test)
    cd "$ROOT/backend" && PYTHONPATH=. python -m pytest
    ;;
  eval)
    cd "$ROOT/backend" && PYTHONPATH=. python -m app.eval_run --dataset "$DATASET" --tag "$TAG" --out "$ROOT/data/eval"
    ;;
  *)
    echo "usage: $0 backend|frontend|storybook|test|eval [dataset] [tag]" >&2
    exit 2
    ;;
esac
