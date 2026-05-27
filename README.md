# Cloud Storage Client

[![CircleCI](https://dl.circleci.com/status-badge/img/gh/ospsd-team-2/ospsd-team-2/tree/hw-1.svg?style=shield)](https://dl.circleci.com/status-badge/redirect/gh/ospsd-team-2/ospsd-team-2/tree/hw-1)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](https://circleci.com/gh/ospsd-team-2/ospsd-team-2)
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)](https://python.org)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

A clean, provider-agnostic Python interface for cloud object storage, with a concrete AWS S3 implementation. Built for NYU OSPSD Spring '26.

---

## What This Project Does

Working with AWS S3 directly means dealing with `boto3`, regions, credentials, multipart uploads, and AWS-specific types — everywhere in your code. This project hides all of that behind a simple, stable interface.

You write code against `CloudStorageClient`. You never import `boto3`. When you need S3, you import `aws_client_impl` once and the implementation wires itself in automatically. If a future team swaps S3 for GCS or Dropbox, none of your application code changes — only the implementation package does.

```python
import aws_client_impl                              # registers S3 via dependency injection
from cloud_storage_client_api.src.factory import get_client

client = get_client()                              # returns an S3Client, typed as CloudStorageClient
files  = client.list_files("")                     # list all objects in your bucket
client.upload_file("local/data.csv", "data.csv")   # upload a file
client.download_file("my-bucket", "data.csv", "local/copy.csv")  # download it back
```

---

## Architecture

The project is a [`uv` workspace](https://docs.astral.sh/uv/concepts/workspaces/) with two installable packages:

```
ospsd-team-2/
├── src/
│   ├── cloud_storage_client_api/   # The abstract interface — no AWS deps, no boto3
│   │   └── src/
│   │       ├── client.py           # CloudStorageClient ABC (the contract)
│   │       └── factory.py          # get_client() / register_client() DI factory
│   └── aws_client_impl/            # The concrete AWS S3 implementation
│       └── src/
│           ├── __init__.py         # Auto-registers with factory on import
│           └── s3_client.py        # S3Client — implements CloudStorageClient via boto3
├── tests/
│   ├── integration/                # Tests that verify DI wiring works end-to-end
│   └── e2e/                        # Full workflow tests against real AWS infrastructure
├── main.py                         # Example entry point demonstrating the full flow
├── pyproject.toml                  # Workspace root — all tool config lives here
└── docs/                           # Sphinx documentation source
```

### How Dependency Injection Works

The interface package (`cloud_storage_client_api`) has **zero knowledge** of AWS or boto3. It only knows about `CloudStorageClient` and the factory.

When you run `import aws_client_impl`, Python executes `aws_client_impl/src/__init__.py`, which calls:

```python
register_client(get_client_impl)
```

From that point forward, `get_client()` returns a fully configured `S3Client`. Your application code never needs to know this happened.

```
┌─────────────────────────┐        ┌──────────────────────────┐
│  cloud_storage_client_api│        │    aws_client_impl        │
│  ─────────────────────── │        │  ───────────────────────  │
│  CloudStorageClient (ABC)│◄───────│  S3Client                 │
│  get_client()            │        │  get_client_impl()        │
│  register_client()       │◄───────│  __init__.py registers    │
└─────────────────────────┘        └──────────────────────────┘
         ▲
         │  callers only ever touch this side
```

---

## Prerequisites

- **Python 3.12+** — check with `python --version`
- **uv** — a fast, all-in-one Python package manager ([install guide](https://docs.astral.sh/uv/getting-started/installation/))
- **An AWS account** — needed for E2E tests and running `main.py` against real S3 (unit and integration tests run without it)

Install `uv`:

```shell
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

---

## Installation & Setup

```shell
git clone git@github.com:ospsd-team-2/ospsd-team-2.git
cd ospsd-team-2
uv sync --all-packages
```

This creates a `.venv` and installs all workspace packages plus dev tools in one step. No `pip`, no manual virtualenv.

---

## AWS Credentials

The client reads all credentials from environment variables. **Never hardcode secrets.**

```shell
export AWS_ACCESS_KEY_ID="your_access_key_id"
export AWS_SECRET_ACCESS_KEY="your_secret_access_key"
export AWS_REGION="us-east-1"
export AWS_BUCKET_NAME="your-bucket-name"
```

You can put these in a local `.env` file — it is listed in `.gitignore` and will never be committed.

> **Tip:** You only need credentials to run `main.py` or the E2E tests against real S3. Unit and integration tests mock all AWS calls and work without credentials.

> **Note:** CI never uses long-lived access keys. The CircleCI pipeline authenticates via OIDC and assumes an IAM role directly. See [CONTRIBUTING.md](CONTRIBUTING.md#cicd) for setup instructions.

---

## Running the Application

With credentials set:

```shell
uv run python main.py
```

This creates a client via DI, lists files in your bucket, and prints them.

---

## Toolchain

All commands run from the project root:

```shell
# Run all tests (unit + integration + e2e)
uv run pytest

# Run only unit tests (no AWS credentials needed)
uv run pytest src/

# Run only integration tests (DI wiring, no AWS needed)
uv run pytest tests/integration/

# Run E2E tests (requires AWS credentials)
uv run pytest tests/e2e/ -m "not local_credentials"

# Lint (ruff — check for issues)
uv run ruff check .

# Format (auto-fix style)
uv run ruff format .

# Type check (mypy strict)
uv run mypy --strict .

# Build docs (Sphinx)
uv run sphinx-build docs/source docs/build/html

# Live-reload docs server
uv run sphinx-autobuild docs/source docs/build/html
```

---

## Documentation

Built with [Sphinx](https://www.sphinx-doc.org/) and the [Furo](https://pradyunsg.me/furo/) theme. Source files live in `docs/source/`. After building, open `docs/build/html/index.html` in your browser.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contribution guide — environment setup, coding standards, testing strategy, how to open a PR, and how to configure AWS for local testing.

See [DESIGN.md](DESIGN.md) for architecture decisions and context.

---

## Dependencies

| Package | Purpose |
|---|---|
| `boto3` | AWS SDK for Python |
| `structlog` | Structured, machine-readable logging |
| `ruff` | Linter and formatter (replaces flake8 + isort + black) |
| `mypy` | Static type checker, run in `--strict` mode |
| `pytest` / `pytest-cov` / `pytest-mock` | Testing framework + coverage + mocking |
| `sphinx` / `furo` / `myst-parser` / `sphinx-autobuild` | Documentation |

---

## Team

| Name | Email |
|---|---|
| Ajay Temal | at5722@nyu.edu |
| Aarav Agrawal | aa10698@nyu.edu |
| Daniel J. Barros | djb10118@nyu.edu |
| Hari Varsha V | hv2241@nyu.edu |
| Nicholas Maspons | nem8891@nyu.edu |

**TAs:** Iván Aristy Eusebio, Adithya Balachandra, Aranya Aryaman

---

## License

MIT — see [`LICENSE`](LICENSE) for details.
