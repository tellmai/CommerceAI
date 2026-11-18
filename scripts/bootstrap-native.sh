#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
candidate="${COMMERCEAI_PYTHON:-python3.12}"
if ! command -v "$candidate" >/dev/null 2>&1; then
  candidate=/home/yang/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3
fi
if [ ! -x "$(command -v "$candidate" 2>/dev/null || echo "$candidate")" ]; then
  echo '需要 Python 3.12，可通过 COMMERCEAI_PYTHON 指定解释器。' >&2
  exit 1
fi
"$candidate" -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.lock -r backend/requirements-dev.lock
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python scripts/native.py start
