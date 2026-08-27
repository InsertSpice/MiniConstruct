#!/usr/bin/env sh
set -eu

BIND_HOST="${MINICONSTRUCT_HOST:-127.0.0.1}"
PORT="${MINICONSTRUCT_PORT:-8743}"

if [ -x ".venv/bin/python" ]; then
  PYTHON_CMD=".venv/bin/python"
else
  PYTHON_CMD="python3"
fi

exec "$PYTHON_CMD" -m miniconstruct --host "$BIND_HOST" --port "$PORT" "$@"

