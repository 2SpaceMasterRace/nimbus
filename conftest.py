"""Shared pytest configuration for repository-wide test setup."""

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("SESSION_SECRET_KEY", "test-session-secret-key")

from aws_client_service.deps import require_oauth_session
from aws_client_service.main import app


@pytest.fixture(autouse=True)
def authenticated_service_app() -> Iterator[None]:
    """Provide a default OAuth session for tests using the shared FastAPI app."""

    def _oauth_token() -> str:
        return "test-github-token"

    app.dependency_overrides[require_oauth_session] = _oauth_token
    yield
    app.dependency_overrides.pop(require_oauth_session, None)
