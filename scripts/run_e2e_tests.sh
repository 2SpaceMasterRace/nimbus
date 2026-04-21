#!/bin/bash
# Run E2E tests with appropriate coverage threshold
# E2E tests only test the entry point, so they don't require high coverage

set -e

uv run pytest \
  tests/e2e \
  --cov-fail-under=0 \
  "$@"
