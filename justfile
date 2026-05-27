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

security-redteam:
    uv run pytest \
      src/ai_server/tests/test_wrapper_contract.py \
      src/nimbus_slack/tests/test_crypto.py \
      src/nimbus_slack/tests/test_main.py \
      src/nimbus_slack/tests/test_store.py \
      src/aws_client_service/aws_client_service/tests/test_security_hardening.py \
      tests/evals/test_runtime_safety_evals.py \
      -q --no-cov
    if command -v gitleaks >/dev/null 2>&1; then gitleaks detect --no-git --redact --source .; else echo "gitleaks not installed; skipped"; fi
    if command -v trufflehog >/dev/null 2>&1; then trufflehog filesystem --no-update --only-verified .; else echo "trufflehog not installed; skipped"; fi
    if command -v semgrep >/dev/null 2>&1; then semgrep scan --config auto --error; else echo "semgrep not installed; skipped"; fi
    if command -v pip-audit >/dev/null 2>&1; then pip-audit; else echo "pip-audit not installed; skipped"; fi

docs host="127.0.0.1" port="8001":
    uv run sphinx-autobuild --host {{host}} --port {{port}} docs/source docs/build/html
