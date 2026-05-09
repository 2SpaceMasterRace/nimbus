"""Tests for Nimbus protocol errors."""

from __future__ import annotations

import pytest

from nimbus_protocol import (
    NimbusError,
    NimbusErrorCategory,
    NimbusErrorCode,
    error_from_exception,
)

pytestmark = pytest.mark.unit


def test_error_has_internal_protocol_and_display_presentations() -> None:
    """A single error should expose three deliberately different views."""
    error = NimbusError(
        code=NimbusErrorCode.POLICY_DENIED,
        category=NimbusErrorCategory.POLICY,
        message="raw detail with object id secret-object",
        display_message="Nimbus needs permission before doing that.",
        retryable=False,
        correlation_id="corr-123",
        http_status=403,
        next_action="Approve the action or choose a narrower target.",
        details={"object": "secret-object"},
    )

    assert error.to_internal()["message"] == "raw detail with object id secret-object"
    assert error.to_internal()["details"] == {"object": "secret-object"}
    assert error.to_protocol() == {
        "code": "policy_denied",
        "category": "policy",
        "message": "Nimbus needs permission before doing that.",
        "retryable": False,
        "correlation_id": "corr-123",
    }
    display = error.to_display()
    assert display.title == "Permission Required"
    assert display.next_action == "Approve the action or choose a narrower target."


def test_error_from_exception_keeps_exception_detail_internal_only() -> None:
    """Translated exceptions should not leak raw detail to protocol clients."""
    error = error_from_exception(
        RuntimeError("token abc123 failed"),
        code=NimbusErrorCode.PROVIDER_FAILED,
        category=NimbusErrorCategory.PROVIDER,
        display_message="The model provider failed.",
        retryable=True,
    )

    assert "token abc123 failed" in str(error.to_internal()["message"])
    assert "token abc123" not in str(error.to_protocol()["message"])
    assert error.to_protocol()["retryable"] is True
