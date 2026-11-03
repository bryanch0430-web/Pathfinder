#!/usr/bin/env bash
# Pathfinder 30 development helpers.
#   ./scripts/dev.sh backend|frontend|storybook|test|eval [dataset] [tag]
#
# The backend, its tests and the evaluation run in backend/.venv, never in whichever python
# is first on PATH. Create it once:
#   python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TASK="${1:-backend}"
DATASET="${2:-hongkong}"
TAG="${3:-baseline-v1}"
PY="$ROOT/backend/.venv/bin/python"
# The backend treats an unset ENVIRONMENT as production and refuses the built-in secrets.
export ENVIRONMENT="${ENVIRONMENT:-development}"

need_venv() {
  if [ ! -x "$PY" ]; then
    echo "Create the venv first: python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt" >&2
    exit 1
  fi
}

case "$TASK" in
  backend)
    need_venv
    cd "$ROOT"
    PYTHONPATH=backend "$PY" -m uvicorn app.main:app --port 8100 --host 127.0.0.1
    ;;
  frontend)
    cd "$ROOT/frontend" && npm run dev
    ;;
  storybook)
    cd "$ROOT/frontend" && npm run storybook
    ;;
  test)
    need_venv
    cd "$ROOT/backend" && PYTHONPATH=. "$PY" -m pytest
    ;;
  eval)
    need_venv
    cd "$ROOT/backend" && PYTHONPATH=. "$PY" -m app.eval_run --dataset "$DATASET" --tag "$TAG" --out "$ROOT/data/eval"
    ;;
  *)
    echo "usage: $0 backend|frontend|storybook|test|eval [dataset] [tag]" >&2
    exit 2
    ;;
esac
