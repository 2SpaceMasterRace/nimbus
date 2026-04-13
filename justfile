set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

default:
    @just --list

docs-build:
    uv run sphinx-build docs/source docs/build/html

bdd:
    uv run pytest tests/bdd -q --no-cov

docs host="127.0.0.1" port="8001":
    uv run sphinx-autobuild --host {{host}} --port {{port}} docs/source docs/build/html
