#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SCHEMA_PATH="${SCHEMA_PATH:-build/openapi/aws_client_service.openapi.json}"
CLIENT_DIR="${CLIENT_DIR:-src/aws_s3_cloud_storage_service_client}"
CLIENT_README="$CLIENT_DIR/README.md"
README_BACKUP=""

restore_readme() {
    if [[ -n "$README_BACKUP" && -f "$README_BACKUP" ]]; then
        mkdir -p "$CLIENT_DIR"
        cp "$README_BACKUP" "$CLIENT_README"
        rm -f "$README_BACKUP"
    fi
}
trap restore_readme EXIT

if [[ ! -f "$SCHEMA_PATH" || "${REFRESH_SCHEMA:-1}" != "0" ]]; then
    ./scripts/update_openapi_schema.sh "$SCHEMA_PATH"
fi

if [[ -f "$CLIENT_README" ]]; then
    README_BACKUP="$(mktemp)"
    cp "$CLIENT_README" "$README_BACKUP"
fi

uvx openapi-python-client generate \
    --path "$SCHEMA_PATH" \
    --meta uv \
    --output-path "$CLIENT_DIR" \
    --overwrite

echo "Regenerated OpenAPI client in $CLIENT_DIR from $SCHEMA_PATH"
