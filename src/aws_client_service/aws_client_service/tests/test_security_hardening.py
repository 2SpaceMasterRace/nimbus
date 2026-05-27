"""Security-hardening tests for public AWS client service surfaces."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


def test_sentry_debug_is_disabled_by_default(client: TestClient) -> None:
    """The deliberate exception route should not be publicly mounted by default."""
    response = client.get("/sentry-debug")

    assert response.status_code == 404


def test_raw_delete_is_disabled_by_default_in_production(
    monkeypatch: pytest.MonkeyPatch,
    client: TestClient,
    mock_storage_client: MagicMock,
) -> None:
    """Production raw storage deletes should require an explicit admin gate."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("NIMBUS_RAW_STORAGE_MUTATIONS_ENABLED", raising=False)

    response = client.delete("/files/my-bucket/my-key")

    assert response.status_code == 403
    mock_storage_client.delete_file.assert_not_called()
