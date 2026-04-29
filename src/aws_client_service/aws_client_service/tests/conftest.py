"""Shared fixtures for aws_client_service endpoint tests."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import MagicMock

import pytest
from aws_client_service.main import app, get_storage_client
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> Iterator[TestClient]:
    """Provide a ``TestClient`` and clear dependency overrides afterwards."""
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def mock_storage_client() -> MagicMock:
    """Inject a mock storage client through the FastAPI dependency system."""
    mock_client = MagicMock()
    app.dependency_overrides[get_storage_client] = lambda: mock_client
    return mock_client
