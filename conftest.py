"""Shared pytest configuration for repository-wide test setup."""

import os
import sys
from collections.abc import Iterator
from importlib import import_module
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    # Keep repository-local test support imports stable under pytest's
    # importlib mode during both focused and full-suite collection.
    sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("SESSION_SECRET_KEY", "test-session-secret-key")

require_oauth_session = import_module("aws_client_service.deps").require_oauth_session
app = import_module("aws_client_service.main").app


@pytest.fixture(autouse=True)
def authenticated_service_app() -> Iterator[None]:
    """Provide a default OAuth session for tests using the shared FastAPI app."""

    def _oauth_token() -> str:
        return "test-github-token"

    app.dependency_overrides[require_oauth_session] = _oauth_token
    yield
    app.dependency_overrides.pop(require_oauth_session, None)
