#!/bin/sh
set -eu

state_file=${1:-ci-state/fly-context.env}

if [ ! -f "$state_file" ]; then
  printf '%s\n' "Rollback state file not found: $state_file" >&2
  exit 1
fi

# shellcheck disable=SC1090
. "$state_file"

if [ -z "${PREVIOUS_RELEASE_IMAGE:-}" ]; then
  printf '%s\n' "No previous Fly release image recorded; skipping rollback." >&2
  exit 1
fi

: "${FLY_APP:?FLY_APP must be set in rollback state}"

flyctl deploy \
  --app "$FLY_APP" \
  --config fly.toml \
  --image "$PREVIOUS_RELEASE_IMAGE" \
  --strategy immediate \
  --wait-timeout 15m \
  --deploy-retries 2 \
  --yes
