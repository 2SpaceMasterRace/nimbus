# syntax=docker/dockerfile:1
#
# Standalone container for the Nimbus Slack Bridge.
#
# The bridge is a workspace package (`src/slack_bridge`) with a workspace
# dependency on `nimbus_runtime`, so the build context must be the
# repository root and the whole workspace must be synced. Build with:
#
#     docker build -f src/slack_bridge/Dockerfile -t nimbus-slack-bridge .

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON=/usr/local/bin/python3.12 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

COPY . /app

# `--all-packages` resolves the entire workspace so the bridge's
# `nimbus-runtime = { workspace = true }` dependency installs from the
# local source tree rather than PyPI.
RUN uv sync --all-packages --frozen --python /usr/local/bin/python3.12

EXPOSE 8080

# Single uvicorn worker keeps the in-memory dedupe cache and any
# future per-process state coherent. Scale out by adding a shared
# dedupe store first (Redis or similar) before increasing replicas.
CMD ["sh", "-c", "uv run uvicorn slack_bridge.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
