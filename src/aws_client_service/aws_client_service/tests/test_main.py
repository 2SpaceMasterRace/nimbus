"""Unit tests for the main FastAPI app and its utility functions."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aws_client_service.main import (
    _debug_routes_enabled,
    _production_environment,
    _raw_mutations_enabled,
    _truthy,
)
from cloud_storage_api import (
    AuthenticationError,
    ContainerNotFoundError,
    InvalidContainerError,
    InvalidFileObjectError,
    InvalidObjectNameError,
    ObjectNotFoundError,
    StorageBackendError,
)
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


# -- _truthy ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        ("True", True),
        ("yes", True),
        ("YES", True),
        ("on", True),
        ("ON", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("", False),
        (None, False),
        ("anything", False),
    ],
)
def test_truthy(value: str | None, expected: bool) -> None:
    assert _truthy(value) is expected


# -- _production_environment ---------------------------------------------------


def test_production_env_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIMBUS_ENV", "production")
    assert _production_environment() is True


def test_production_env_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIMBUS_ENV", "prod")
    assert _production_environment() is True


@pytest.mark.parametrize("env", ["development", "", "staging"])
def test_production_env_other(env: str, monkeypatch: pytest.MonkeyPatch) -> None:
    if env:
        monkeypatch.setenv("NIMBUS_ENV", env)
    else:
        monkeypatch.delenv("NIMBUS_ENV", raising=False)
    assert _production_environment() is False


def test_production_env_fallback_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NIMBUS_ENV", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    assert _production_environment() is True


# -- _raw_mutations_enabled ---------------------------------------------------


def test_raw_mutations_disabled_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NIMBUS_ENV", "production")
    assert _raw_mutations_enabled() is False


def test_raw_mutations_explicitly_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIMBUS_RAW_STORAGE_MUTATIONS_ENABLED", "true")
    assert _raw_mutations_enabled() is True


def test_raw_mutations_explicitly_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIMBUS_RAW_STORAGE_MUTATIONS_ENABLED", "false")
    monkeypatch.setenv("NIMBUS_ENV", "production")
    assert _raw_mutations_enabled() is False


def test_raw_mutations_default_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIMBUS_ENV", "development")
    monkeypatch.delenv("NIMBUS_RAW_STORAGE_MUTATIONS_ENABLED", raising=False)
    assert _raw_mutations_enabled() is True


# -- _debug_routes_enabled ----------------------------------------------------


def test_debug_routes_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIMBUS_ENABLE_DEBUG_ROUTES", "true")
    assert _debug_routes_enabled() is True


def test_debug_routes_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NIMBUS_ENABLE_DEBUG_ROUTES", "false")
    assert _debug_routes_enabled() is False


def test_debug_routes_not_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NIMBUS_ENABLE_DEBUG_ROUTES", raising=False)
    assert _debug_routes_enabled() is False


# -- /ready endpoint ----------------------------------------------------------


def _ready_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_SERVER_API_KEY", "dev-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("SESSION_SECRET_KEY", "s3cr3t")
    monkeypatch.setenv("API_KEY", "ap1k3y")
    monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", "s1gn1ng")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret-key")
    monkeypatch.setenv("AWS_REGION", "us-east-1")


def test_ready_returns_ok(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    _ready_env(monkeypatch)
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_ready_fails_on_missing_env(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    monkeypatch.delenv("SESSION_SECRET_KEY", raising=False)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setenv("AI_SERVER_API_KEY", "dev-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    response = client.get("/ready")
    assert response.status_code == 503
    assert "failures" in response.json()["detail"]


def test_ready_fails_in_production_without_admin_key(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    _ready_env(monkeypatch)
    monkeypatch.setenv("NIMBUS_ENV", "production")
    monkeypatch.setenv("NIMBUS_RAW_STORAGE_MUTATIONS_ENABLED", "true")
    monkeypatch.delenv("NIMBUS_RAW_STORAGE_ADMIN_KEY", raising=False)
    response = client.get("/ready")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "NIMBUS_RAW_STORAGE_ADMIN_KEY" in str(detail)


# -- upload error-handling paths ----------------------------------------------


def test_upload_returns_400_on_invalid_container(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.upload_obj.side_effect = InvalidContainerError("bad container")
    response = client.post(
        "/files/my-bucket/photo.jpg",
        files={"file": ("photo.jpg", b"data", "image/jpeg")},
    )
    assert response.status_code == 400


def test_upload_returns_400_on_invalid_file(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.upload_obj.side_effect = InvalidFileObjectError("bad file")
    response = client.post(
        "/files/my-bucket/photo.jpg",
        files={"file": ("photo.jpg", b"data", "image/jpeg")},
    )
    assert response.status_code == 400


def test_upload_returns_404_on_missing_container(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.upload_obj.side_effect = ContainerNotFoundError(
        "no such bucket"
    )
    response = client.post(
        "/files/my-bucket/photo.jpg",
        files={"file": ("photo.jpg", b"data", "image/jpeg")},
    )
    assert response.status_code == 404


def test_upload_returns_401_on_auth_error(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.upload_obj.side_effect = AuthenticationError("bad creds")
    response = client.post(
        "/files/my-bucket/photo.jpg",
        files={"file": ("photo.jpg", b"data", "image/jpeg")},
    )
    assert response.status_code == 401


# -- delete error-handling paths ----------------------------------------------


def test_delete_returns_400_on_invalid_container(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.delete_file.side_effect = InvalidContainerError("bad container")
    response = client.delete("/files/my-bucket/my-key")
    assert response.status_code == 400


def test_delete_returns_400_on_invalid_key(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.delete_file.side_effect = InvalidObjectNameError("bad key")
    response = client.delete("/files/my-bucket/my-key")
    assert response.status_code == 400


def test_delete_returns_401_on_auth_error(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.delete_file.side_effect = AuthenticationError("bad creds")
    response = client.delete("/files/my-bucket/my-key")
    assert response.status_code == 401


# -- list error-handling paths ------------------------------------------------


def test_list_returns_401_on_auth_error(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.list_files.side_effect = AuthenticationError("bad creds")
    response = client.get("/files", params={"container": "bucket"})
    assert response.status_code == 401


def test_list_returns_404_on_missing_container(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.list_files.side_effect = ContainerNotFoundError("no bucket")
    response = client.get("/files", params={"container": "bucket"})
    assert response.status_code == 404


# -- get_file_info error-handling paths ---------------------------------------


def test_get_info_returns_400_on_invalid_container(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.get_file_info.side_effect = InvalidContainerError("bad")
    response = client.get("/files/my-bucket/my-key/info")
    assert response.status_code == 400


def test_get_info_returns_400_on_invalid_key(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.get_file_info.side_effect = InvalidObjectNameError("bad")
    response = client.get("/files/my-bucket/my-key/info")
    assert response.status_code == 400


def test_get_info_returns_404_on_missing_object(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.get_file_info.side_effect = ObjectNotFoundError("not found")
    response = client.get("/files/my-bucket/my-key/info")
    assert response.status_code == 404


def test_get_info_returns_404_on_missing_container(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.get_file_info.side_effect = ContainerNotFoundError("no bucket")
    response = client.get("/files/my-bucket/my-key/info")
    assert response.status_code == 404


def test_get_info_returns_401_on_auth_error(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.get_file_info.side_effect = AuthenticationError("bad creds")
    response = client.get("/files/my-bucket/my-key/info")
    assert response.status_code == 401


def test_get_info_returns_502_on_storage_error(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.get_file_info.side_effect = StorageBackendError("backend down")
    response = client.get("/files/my-bucket/my-key/info")
    assert response.status_code == 502


# -- download error-handling paths --------------------------------------------


def test_download_returns_400_on_invalid_container(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.download_file.side_effect = InvalidContainerError("bad")
    response = client.get(
        "/download", params={"container": "bucket", "object_name": "key.txt"}
    )
    assert response.status_code == 400


def test_download_returns_400_on_invalid_key(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.download_file.side_effect = InvalidObjectNameError("bad")
    response = client.get(
        "/download", params={"container": "bucket", "object_name": "key.txt"}
    )
    assert response.status_code == 400


def test_download_returns_404_on_missing_object(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.download_file.side_effect = ObjectNotFoundError("not found")
    response = client.get(
        "/download", params={"container": "bucket", "object_name": "key.txt"}
    )
    assert response.status_code == 404


def test_download_returns_404_on_missing_container(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.download_file.side_effect = ContainerNotFoundError("no bucket")
    response = client.get(
        "/download", params={"container": "bucket", "object_name": "key.txt"}
    )
    assert response.status_code == 404


def test_download_returns_401_on_auth_error(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.download_file.side_effect = AuthenticationError("bad creds")
    response = client.get(
        "/download", params={"container": "bucket", "object_name": "key.txt"}
    )
    assert response.status_code == 401


def test_download_returns_502_on_storage_error(
    client: TestClient, mock_storage_client: MagicMock
) -> None:
    mock_storage_client.download_file.side_effect = StorageBackendError("backend down")
    response = client.get(
        "/download", params={"container": "bucket", "object_name": "key.txt"}
    )
    assert response.status_code == 502


# -- sentry-debug endpoint ----------------------------------------------------


def test_sentry_debug_disabled_returns_404(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NIMBUS_ENABLE_DEBUG_ROUTES", "false")
    response = client.get("/sentry-debug")
    assert response.status_code == 404


def test_sentry_debug_with_admin_key_succeeds(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NIMBUS_ENABLE_DEBUG_ROUTES", "true")
    monkeypatch.setenv("NIMBUS_RAW_STORAGE_ADMIN_KEY", "admin-key")
    with pytest.raises(RuntimeError, match="Intentional Sentry debug exception"):
        client.get(
            "/sentry-debug",
            headers={"X-Nimbus-Storage-Admin-Key": "admin-key"},
        )


def test_sentry_debug_with_admin_key_unauthorized(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NIMBUS_ENABLE_DEBUG_ROUTES", "true")
    monkeypatch.setenv("NIMBUS_RAW_STORAGE_ADMIN_KEY", "admin-key")
    response = client.get("/sentry-debug")
    assert response.status_code == 401


def test_sentry_debug_no_admin_key_production(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NIMBUS_ENABLE_DEBUG_ROUTES", "true")
    monkeypatch.setenv("NIMBUS_ENV", "production")
    monkeypatch.delenv("NIMBUS_RAW_STORAGE_ADMIN_KEY", raising=False)
    response = client.get("/sentry-debug", headers={"X-API-Key": "ap1k3y"})
    assert response.status_code == 503


def test_sentry_debug_no_admin_key_dev_with_api_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NIMBUS_ENABLE_DEBUG_ROUTES", "true")
    monkeypatch.setenv("NIMBUS_ENV", "development")
    monkeypatch.setenv("API_KEY", "dev-api-key")
    monkeypatch.delenv("NIMBUS_RAW_STORAGE_ADMIN_KEY", raising=False)
    with pytest.raises(RuntimeError, match="Intentional Sentry debug exception"):
        client.get("/sentry-debug", headers={"X-API-Key": "dev-api-key"})


def test_sentry_debug_no_admin_key_dev_no_auth(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NIMBUS_ENABLE_DEBUG_ROUTES", "true")
    monkeypatch.setenv("NIMBUS_ENV", "development")
    monkeypatch.delenv("NIMBUS_RAW_STORAGE_ADMIN_KEY", raising=False)
    response = client.get("/sentry-debug")
    assert response.status_code == 401


# -- readiness production edge cases ------------------------------------------


def test_ready_fails_in_production_without_signing_secret(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SESSION_SECRET_KEY", "s3cr3t")
    monkeypatch.setenv("API_KEY", "ap1k3y")
    monkeypatch.setenv("AI_SERVER_API_KEY", "dev-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("NIMBUS_ENV", "production")
    monkeypatch.delenv("AI_SERVER_SIGNING_SECRET", raising=False)
    response = client.get("/ready")
    assert response.status_code == 503
    detail = response.json()["detail"]
    failures = detail["failures"]
    assert any("AI_SERVER_SIGNING_SECRET" in f for f in failures)


def test_ready_fails_in_production_without_kms_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SESSION_SECRET_KEY", "s3cr3t")
    monkeypatch.setenv("API_KEY", "ap1k3y")
    monkeypatch.setenv("AI_SERVER_API_KEY", "dev-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("NIMBUS_ENV", "production")
    monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", "s1gn1ng")
    monkeypatch.setenv("NIMBUS_RAW_STORAGE_MUTATIONS_ENABLED", "false")
    monkeypatch.delenv("NIMBUS_S3_KMS_KEY_ID", raising=False)
    response = client.get("/ready")
    assert response.status_code == 503
    detail = response.json()["detail"]
    failures = detail["failures"]
    assert any("NIMBUS_S3_KMS_KEY_ID" in f for f in failures)


def test_ready_succeeds_in_production_with_all_env(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SESSION_SECRET_KEY", "s3cr3t")
    monkeypatch.setenv("API_KEY", "ap1k3y")
    monkeypatch.setenv("AI_SERVER_API_KEY", "dev-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("NIMBUS_ENV", "production")
    monkeypatch.setenv("AI_SERVER_SIGNING_SECRET", "s1gn1ng")
    monkeypatch.setenv("NIMBUS_RAW_STORAGE_MUTATIONS_ENABLED", "false")
    monkeypatch.setenv("NIMBUS_S3_KMS_KEY_ID", "some-kms-key")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test-access-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret-key")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
