#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SCHEMA_PATH="${1:-${SCHEMA_PATH:-build/openapi/aws_client_service.openapi.json}}"
mkdir -p "$(dirname "$SCHEMA_PATH")"

export SESSION_SECRET_KEY="${SESSION_SECRET_KEY:-dev-session-secret}"
export API_KEY="${API_KEY:-dev-storage-api-key}"
export AI_SERVER_API_KEY="${AI_SERVER_API_KEY:-dev-ai-api-key}"
export AI_SERVER_SIGNING_SECRET="${AI_SERVER_SIGNING_SECRET:-dev-wrapper-signing-secret}"

uv run python - "$SCHEMA_PATH" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

from aws_client_service.main import app

schema_path = Path(sys.argv[1])
schema = app.openapi()
schema_path.write_text(
    json.dumps(schema, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
print(f"Wrote OpenAPI schema to {schema_path}")
PY
