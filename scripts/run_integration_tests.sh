#!/bin/bash
# Run integration tests with appropriate coverage threshold
# Integration tests only test DI wiring, so they don't require high coverage

set -e

uv run pytest \
  tests/integration/ \
  --cov-fail-under=0 \
  "$@"
