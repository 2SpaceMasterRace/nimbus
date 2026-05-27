"""Unit tests for aws_client_service authentication dependencies."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from aws_client_service.deps import (
    _extract_bearer_token,
    _production_environment,
    _raw_mutations_enabled,
    _truthy,
    require_oauth_session,
    require_storage_mutation_admin,
)
from fastapi import HTTPException, Request

pytestmark = pytest.mark.unit


class TestExtractBearerToken:
    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("Bearer token123", "token123"),
            ("bearer token123", "token123"),
            ("Bearer ", None),
            (None, None),
            ("Basic dXNlcjpwYXNz", None),
            ("", None),
        ],
    )
    def test_extract_bearer_token(
        self, header: str | None, expected: str | None
    ) -> None:
        assert _extract_bearer_token(header) == expected


class TestTruthy:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("1", True),
            ("true", True),
            ("True", True),
            ("yes", True),
            ("on", True),
            ("0", False),
            ("false", False),
            ("no", False),
            (None, False),
            ("", False),
        ],
    )
    def test_truthy(self, value: str | None, expected: bool) -> None:
        assert _truthy(value) is expected


class TestProductionEnvironment:
    def test_production_nimbus_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NIMBUS_ENV", "production")
        assert _production_environment() is True

    def test_environment_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NIMBUS_ENV", raising=False)
        monkeypatch.setenv("ENVIRONMENT", "prod")
        assert _production_environment() is True

    def test_app_env_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NIMBUS_ENV", raising=False)
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.setenv("APP_ENV", "production")
        assert _production_environment() is True

    def test_non_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NIMBUS_ENV", "development")
        assert _production_environment() is False


class TestRawMutationsEnabled:
    def test_configured_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NIMBUS_RAW_STORAGE_MUTATIONS_ENABLED", "true")
        assert _raw_mutations_enabled() is True

    def test_configured_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NIMBUS_RAW_STORAGE_MUTATIONS_ENABLED", "false")
        assert _raw_mutations_enabled() is False

    def test_not_configured_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NIMBUS_RAW_STORAGE_MUTATIONS_ENABLED", raising=False)
        monkeypatch.setenv("NIMBUS_ENV", "production")
        assert _raw_mutations_enabled() is False

    def test_not_configured_dev(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("NIMBUS_RAW_STORAGE_MUTATIONS_ENABLED", raising=False)
        monkeypatch.setenv("NIMBUS_ENV", "development")
        assert _raw_mutations_enabled() is True


class TestRequireOAuthSession:
    def test_missing_header_raises(self) -> None:
        request = MagicMock(spec=Request)
        request.session = {}
        with pytest.raises(HTTPException) as exc:
            require_oauth_session(request)
        assert exc.value.status_code == 401

    @patch.dict(os.environ, {"API_KEY": "test-key"})
    def test_valid_api_key_header(self) -> None:
        request = MagicMock(spec=Request)
        request.session = {}
        result = require_oauth_session(request, x_api_key="test-key")
        assert result == "test-key"

    @patch.dict(os.environ, {"API_KEY": "test-key"})
    def test_valid_bearer_token(self) -> None:
        request = MagicMock(spec=Request)
        request.session = {}
        result = require_oauth_session(
            request,
            authorization="Bearer test-key",
        )
        assert result == "test-key"

    def test_session_auth_success(self) -> None:
        request = MagicMock(spec=Request)
        request.session = {"github_session_id": "sess-123"}
        with patch(
            "aws_client_service.deps.get_oauth_session",
            return_value=MagicMock(access_token="oauth-token"),  # noqa: S106
        ):
            result = require_oauth_session(request)
            assert result == "oauth-token"

    def test_session_auth_invalid_session(self) -> None:
        request = MagicMock(spec=Request)
        request.session = {"github_session_id": "sess-123"}
        with patch(
            "aws_client_service.deps.get_oauth_session",
            return_value=None,
        ):
            with pytest.raises(HTTPException) as exc:
                require_oauth_session(request)
            assert exc.value.status_code == 401

    def test_api_key_mismatch_falls_through(self) -> None:
        request = MagicMock(spec=Request)
        request.session = {}
        with patch.dict(os.environ, {"API_KEY": "real-key"}):
            with pytest.raises(HTTPException) as exc:
                require_oauth_session(request, x_api_key="wrong-key")
            assert exc.value.status_code == 401


class TestRequireStorageMutationAdmin:
    def test_mutations_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NIMBUS_RAW_STORAGE_MUTATIONS_ENABLED", "false")
        with pytest.raises(HTTPException) as exc:
            require_storage_mutation_admin(
                MagicMock(spec=Request),
                "base-auth-token",
            )
        assert exc.value.status_code == 403

    def test_valid_admin_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NIMBUS_RAW_STORAGE_MUTATIONS_ENABLED", "true")
        monkeypatch.setenv("NIMBUS_RAW_STORAGE_ADMIN_KEY", "admin-key")
        result = require_storage_mutation_admin(
            MagicMock(spec=Request),
            "base-auth-token",
            x_storage_admin_key="admin-key",
        )
        assert result == "raw-storage-admin"

    def test_invalid_admin_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NIMBUS_RAW_STORAGE_MUTATIONS_ENABLED", "true")
        monkeypatch.setenv("NIMBUS_RAW_STORAGE_ADMIN_KEY", "admin-key")
        with pytest.raises(HTTPException) as exc:
            require_storage_mutation_admin(
                MagicMock(spec=Request),
                "base-auth-token",
                x_storage_admin_key="wrong-key",
            )
        assert exc.value.status_code == 401

    def test_no_admin_key_production(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NIMBUS_RAW_STORAGE_MUTATIONS_ENABLED", "true")
        monkeypatch.delenv("NIMBUS_RAW_STORAGE_ADMIN_KEY", raising=False)
        monkeypatch.setenv("NIMBUS_ENV", "production")
        with pytest.raises(HTTPException) as exc:
            require_storage_mutation_admin(
                MagicMock(spec=Request),
                "base-auth-token",
            )
        assert exc.value.status_code == 503

    def test_no_admin_key_dev(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("NIMBUS_RAW_STORAGE_MUTATIONS_ENABLED", "true")
        monkeypatch.delenv("NIMBUS_RAW_STORAGE_ADMIN_KEY", raising=False)
        monkeypatch.setenv("NIMBUS_ENV", "development")
        result = require_storage_mutation_admin(
            MagicMock(spec=Request),
            "base-auth-token",
        )
        assert result == "base-auth-token"
