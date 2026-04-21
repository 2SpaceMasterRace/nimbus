# Contributing to ospsd-team-2

Thank you for taking the time to contribute. This guide covers everything you need — from setting up your environment for the first time, to running tests with real AWS credentials, to getting your pull request merged.

We welcome contributions of all kinds: bug fixes, new features, documentation improvements, and code review. If you are new to the project, issues labeled [`good first issue`](https://github.com/ospsd-team-2/ospsd-team-2/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) are a great place to start.

---

## Table of Contents

1. [What This Project Does](#what-this-project-does)
2. [Environment Setup](#environment-setup)
3. [AWS Credentials](#aws-credentials)
4. [Running the Code](#running-the-code)
5. [Running Tests](#running-tests)
6. [Linting and Type Checking](#linting-and-type-checking)
7. [Project Structure](#project-structure)
8. [Making Changes](#making-changes)
9. [Opening a Pull Request](#opening-a-pull-request)
10. [Reporting Issues](#reporting-issues)
11. [CI/CD](#cicd)

---

## What This Project Does

This library wraps AWS S3 behind a clean, provider-agnostic Python interface. The goal is to let application code work with cloud storage without knowing anything about boto3, regions, or multipart upload mechanics.

The project is split into three packages:

- **`cloud_storage_client_api`** — defines the abstract `CloudStorageClient` base class (the contract). No AWS deps, no boto3. Any cloud provider could implement this.
- **`aws_client_impl`** — the concrete S3 implementation. Uses boto3 under the hood. Wires itself into the interface automatically via **Dependency Injection** when imported.
- **`aws_client_service`** — a FastAPI HTTP service that exposes `aws_client_impl` over HTTP. Supports file upload, download, and delete via REST endpoints.

The DI pattern means callers only ever touch the interface:

```python
import aws_client_impl                               # registers S3 as a side effect
from cloud_storage_client_api.factory import get_client

client = get_client()                               # returns S3Client, typed as CloudStorageClient
client.upload_file("report.csv", "reports/q1.csv")
```

Swapping to a different provider in the future only requires writing a new implementation package — no application code changes.

---

## Environment Setup

### 1. Install Python 3.12+

Check your version:

```shell
python --version   # needs to be 3.12 or higher
```

If you need to upgrade, use [pyenv](https://github.com/pyenv/pyenv) or download from [python.org](https://www.python.org/downloads/).

### 2. Install uv

`uv` is the package manager for this project. It manages virtual environments, dependencies, and workspace packages. Do not use `pip` directly.

```shell
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Verify:

```shell
uv --version
```

### 3. Clone and install

```shell
git clone git@github.com:ospsd-team-2/ospsd-team-2.git
cd ospsd-team-2
uv sync --all-packages
```

`uv sync` creates a `.venv` and installs every workspace package plus all dev tools (pytest, ruff, mypy, sphinx, etc.) in one step. You do not need to activate the virtualenv manually — prefix commands with `uv run` and it handles that automatically.

---

## AWS Credentials

Unit tests and integration tests are fully mocked — **you do not need AWS credentials to run most of the test suite.**

You only need credentials to:
- Run `main.py` against a real S3 bucket
- Run E2E tests (`tests/e2e/test_main_script_runs_successfully`)

### Setting credentials

The client reads credentials exclusively from environment variables. Never hardcode them.

```shell
export AWS_ACCESS_KEY_ID="AKIA..."
export AWS_SECRET_ACCESS_KEY="your_secret"
export AWS_REGION="us-east-1"
export AWS_BUCKET_NAME="your-test-bucket"
```

For convenience, create a `.env` file in the repo root (it is in `.gitignore` and will never be committed):

```shell
# .env — local only, never commit this file
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=your_secret
AWS_REGION=us-east-1
AWS_BUCKET_NAME=your-test-bucket
```

Then load it before running:

```shell
set -a && source .env && set +a
```

### Getting AWS credentials

If you don't have credentials yet:

1. Log in to the [AWS Console](https://console.aws.amazon.com/).
2. Go to **IAM → Users → your user → Security credentials → Create access key**.
3. Choose "Local code" as the use case.
4. Copy the `Access key ID` and `Secret access key` — you only see the secret once.
5. Create an S3 bucket in your account (or ask a teammate for access to the shared test bucket).

For the E2E tests to pass, the IAM user or role needs at minimum: `s3:ListBucket`, `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject`.

### Verify your credentials work

```shell
uv run python main.py
```

You should see structured log output listing the files in your bucket. If you see a `KeyError: 'AWS_BUCKET_NAME'` or a `NoCredentialsError`, your environment variables are not set correctly.

---

## Running the Code

With credentials set:

```shell
uv run python main.py
```
**FastAPI service** — starts the HTTP server on port 8000:
```shell
uv run uvicorn aws_client_service.main:app --reload
```

Upload an object:
```shell
curl -X POST "http://localhost:8000/files/my-bucket/data.csv" \
  -F "file=@/path/to/local/data.csv"
```

Then download a file:
```shell
curl "http://localhost:8000/download?bucket_name=my-bucket&object_name=data.csv" \
  --output data.csv
```

Delete an object:
```shell
curl -X DELETE "http://localhost:8000/files/my-bucket/data.csv"
```

This demonstrates the full flow: DI wiring → client creation → S3 API call → response handling.

---

## Running Tests

The project has three test layers. Run them all at once or individually:

```shell
# Run everything
uv run pytest

# Unit tests only — fast, fully mocked, no AWS needed
uv run pytest src/

# Integration tests only — verify DI wiring, no AWS needed
uv run pytest tests/integration/

# E2E tests — requires AWS credentials
uv run pytest tests/e2e/ -m "not local_credentials"

# Run with verbose output
uv run pytest -v

# Run a specific test file
uv run pytest src/aws_client_impl/tests/test_upload_file.py -v

# Run tests matching a keyword
uv run pytest -k "upload" -v
```

### Test markers

Tests are tagged with markers defined in `pyproject.toml`:

| Marker | Meaning |
|---|---|
| `unit` | Fast, isolated, no external deps |
| `integration` | Verifies component wiring |
| `e2e` | Requires real AWS infrastructure |
| `circleci` | Safe to run in CI without local credentials |
| `local_credentials` | Requires local `credentials.json` or `token.json` |

```shell
# Run only unit tests
uv run pytest -m unit

# Run everything except tests requiring local credential files
uv run pytest -m "not local_credentials"
```

### Coverage

Coverage is measured automatically when you run `pytest`. The threshold is set to 80% in `pyproject.toml`. The current coverage is 100%.

```shell
# Show coverage in terminal (already default)
uv run pytest --cov-report=term-missing

# Generate HTML report and open it
uv run pytest --cov-report=html && open htmlcov/index.html
```

---

## Linting and Type Checking

All of these must pass before you open a PR. They also run automatically in CI.

```shell
# Check for lint issues
uv run ruff check .

# Auto-fix safe lint issues
uv run ruff check . --fix

# Check formatting (does not modify files)
uv run ruff format --check .

# Apply formatting
uv run ruff format .

# Type check (strict)
uv run mypy --strict .
```

This project uses `ruff` with `select = ["ALL"]` — the strictest ruleset. If you need to suppress a rule, add a `# noqa: RULE_CODE  # Justification: reason` comment inline. All existing suppressions have justifications — follow that pattern. Blanket `# noqa` without a code is not accepted.

---

## Project Structure

```
ospsd-team-2/
├── src/
│   ├── cloud_storage_client_api/       # Abstract interface package
│   │   ├── pyproject.toml
│   │   └── cloud_storage_client_api/
│   │       ├── __init__.py
│   │       ├── client.py               # CloudStorageClient ABC
│   │       └── factory.py              # register_client() / get_client()
│   ├── aws_client_impl/                # S3 implementation package
│   │   ├── pyproject.toml
│   │   ├── tests/                      # Unit tests for this package
│   │   └── aws_client_impl/
│   │       ├── __init__.py             # Registers with factory on import
│   │       └── s3_client.py            # S3Client implementation
│   └── aws_client_service/             # FastAPI HTTP service
│       ├── pyproject.toml
│       └── aws_client_service/
│           └── main.py                 # GET /health, GET /, POST /files/..., GET /download, DELETE /files/...
├── tests/
│   ├── integration/                    # DI wiring tests
│   └── e2e/                            # Full end-to-end tests
├── docs/
│   └── source/                         # Sphinx documentation source
│       ├── conf.py
│       ├── index.md
│       ├── api.md
│       ├── getting-started.md
│       ├── CONTRIBUTING.md
│       └── DESIGN.md
├── .circleci/config.yml                # CI pipeline
├── .github/
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── ISSUE_TEMPLATE/
├── main.py                             # Example entry point
├── pyproject.toml                      # Workspace root + all tool config
└── uv.lock                             # Locked dependencies
```

**Important:** All tool configuration (ruff, mypy, pytest, coverage) lives in the **root `pyproject.toml` only**. Do not add tool config to the sub-package `pyproject.toml` files.

---

## Making Changes

### Branch naming

Create a branch from `main` using a short, descriptive name:

```shell
git checkout main
git pull origin main
git checkout -b fix-upload-error-handling
```

Suggested prefixes: `feat/`, `fix/`, `docs/`, `test/`, `refactor/`

### Commit messages

Write commits in the **imperative mood**, as if completing the sentence "This commit will...":

```
# Good
add retry logic to upload_file
fix multipart upload part numbering bug
update CONTRIBUTING.md with AWS setup steps

# Bad
added retry logic
fixed a bug
updates
```

Keep commits small and focused. Each commit should represent one logical change. Clean up your history before opening a PR — squash "fix typo" and "WIP" commits.

### Before pushing

Run this checklist locally:

```shell
uv run ruff check .          # no lint errors
uv run ruff format --check . # no formatting issues
uv run mypy --strict .       # no type errors
uv run pytest                # all tests pass, coverage >= 80%
```

---

## Opening a Pull Request

1. Push your branch and open a PR from `hw-1` (or your feature branch) → `main`.
2. **Do not merge your own PR.** A teammate must review and approve.
3. Fill in the PR description following `.github/PULL_REQUEST_TEMPLATE.md`. Include:
   - What problem this solves / what issue it closes
   - What changed and why
   - Whether tests were added or updated
4. Keep PRs small and focused. If your change is large, split it into a series of smaller PRs. Reviewers struggle with 500+ line diffs.
5. Respond to all review comments — even if you disagree. If you disagree, explain why. Don't just push changes silently.
6. Once approved, the reviewer merges.

### PR checklist

- [ ] Branch is up to date with `main`
- [ ] `uv run ruff check .` passes
- [ ] `uv run ruff format --check .` passes
- [ ] `uv run mypy --strict .` passes
- [ ] `uv run pytest` passes with coverage >= 80%
- [ ] PR description follows the template
- [ ] New `# noqa` suppressions have inline justifications

---

## Reporting Issues

Before filing an issue, search [existing issues](https://github.com/ospsd-team-2/ospsd-team-2/issues) to avoid duplicates.

A good bug report includes:

- A clear description of what went wrong
- The exact command you ran
- The full error output (stack trace)
- Your Python version (`python --version`) and OS
- A minimal code example that reproduces the problem

A good feature request includes:

- What you are trying to do and why the current interface doesn't support it
- A concrete example of what the new API would look like
- Whether you are willing to implement it yourself

Use the issue templates — they prompt you for this information.

---

## CI/CD

We use [CircleCI](https://circleci.com/). The pipeline runs on every push to any branch.

### What runs in CI

| Job | What it does |
|---|---|
| `lint` | `ruff check .`, `ruff format --check .`, `mypy --strict .` |
| `unit-tests` | All tests under `src/*/tests/` with coverage reporting |
| `integration-tests` | Tests under `tests/integration/` |
| `e2e-tests` | Tests under `tests/e2e/` against real AWS (uses `aws-ospsd` context) |

### Viewing results

Test results and coverage reports are uploaded as artifacts and visible in the CircleCI UI under the **Tests** and **Artifacts** tabs for each job.

### Setting up the AWS context for E2E tests

The E2E job uses a CircleCI context named `aws-ospsd`. CI authenticates to AWS using OIDC — no long-lived access keys are stored anywhere.

> **Note:** CI never uses long-lived access keys. The CircleCI pipeline authenticates via OIDC and assumes an IAM role directly. The credentials you create here are for local development only.

#### 1. Create the IAM OIDC Identity Provider

1. Go to **AWS Console → IAM → Identity providers → Add provider**.
2. Choose **OpenID Connect**.
3. Set the **Provider URL** to `https://oidc.circleci.com/org/<your-circleci-org-id>` (your CircleCI organization ID is found under CircleCI → Organization Settings).
4. Click **Get thumbprint**.
5. Set the **Audience** to `https://oidc.circleci.com/org/<your-circleci-org-id>`.
6. Click **Add provider**.

#### 2. Create the IAM Role

1. Go to **IAM → Roles → Create role**.
2. Choose **Web identity** as the trusted entity type and select the identity provider you just created.
3. Use this trust policy, replacing the placeholders:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<your-aws-account-id>:oidc-provider/oidc.circleci.com/org/<your-circleci-org-id>"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringLike": {
          "oidc.circleci.com/org/<your-circleci-org-id>:sub": "org/<your-circleci-org-id>/project/<your-circleci-project-id>/user/*"
        }
      }
    }
  ]
}
```

4. Attach a policy granting the minimum S3 permissions the tests need:
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": [
        "arn:aws:s3:::<your-bucket-name>",
        "arn:aws:s3:::<your-bucket-name>/*"
      ]
    }
  ]
}
```

5. Name the role (e.g. `circleci-ospsd-e2e`) and save the ARN — you will need it in the next step.

#### 3. Create the CircleCI context

1. Go to CircleCI → **Organization Settings** → **Contexts** → **Create Context** named `aws-ospsd`.
2. Add these environment variables to the context:
   - `AWS_ROLE_ARN` — the role ARN from step 2 above
   - `AWS_REGION` — e.g. `us-east-1`
   - `AWS_BUCKET_NAME` — the test bucket name
3. Do **not** add `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` — the pipeline assumes the role via OIDC automatically.

If you do not have access to the CircleCI organization, ask a team member to add you.
