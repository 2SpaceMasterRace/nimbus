#!/usr/bin/env sh
set -eu

if [ "${NIMBUS_STATE_BACKEND:-}" = "postgres" ]; then
  uv run python scripts/db/migrate.py
fi

exec uv run uvicorn aws_client_service.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8080}"
