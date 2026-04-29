#!/usr/bin/env sh
set -eu

exec uv run uvicorn nimbus_slack.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8080}"
