set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

default:
    @just --list

setup:
    uv sync --all-packages

format:
    uv run ruff check --fix .
    uv run ruff format .

lint:
    uv run ruff check .
    uv run mypy --strict .

test:
    uv run pytest

docs-build:
    uv run sphinx-build docs/source docs/build/html

docs-check: docs-build

bdd:
    uv run pytest tests/bdd -q --no-cov

nimbus:
    uv run python main.py

smoke-wrapper:
    uv run python scripts/ai_server_wrapper_smoke.py

docs host="127.0.0.1" port="8001":
    uv run sphinx-autobuild --host {{host}} --port {{port}} docs/source docs/build/html
