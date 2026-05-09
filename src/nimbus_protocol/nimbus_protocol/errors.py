"""Stable Nimbus error presentations.

The same failure needs three different shapes:

* an internal diagnostic record for logs and tests;
* a redacted protocol payload for HTTP, CLI, Slack, and replay;
* a human-facing display message with a suggested next action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


class NimbusErrorCategory(StrEnum):
    """Broad class of a Nimbus failure."""

    AUTH = "auth"
    CONFIG = "config"
    VALIDATION = "validation"
    POLICY = "policy"
    PROVIDER = "provider"
    STORAGE = "storage"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    CONFLICT = "conflict"
    INTERNAL = "internal"


class NimbusErrorCode(StrEnum):
    """Stable error codes that clients can branch on."""

    AUTH_REQUIRED = "auth_required"
    CONFIG_MISSING = "config_missing"
    VALIDATION_FAILED = "validation_failed"
    POLICY_DENIED = "policy_denied"
    PROVIDER_FAILED = "provider_failed"
    STORAGE_FAILED = "storage_failed"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    IN_FLIGHT = "in_flight"
    INTERNAL = "internal"


@dataclass(frozen=True, slots=True)
class NimbusDisplayError:
    """Human-facing error text for terminal or chat rendering."""

    title: str
    message: str
    next_action: str | None = None


@dataclass(frozen=True, slots=True)
class NimbusError:
    """One Nimbus failure with internal, protocol, and display views."""

    code: NimbusErrorCode
    category: NimbusErrorCategory
    message: str
    display_message: str
    retryable: bool
    correlation_id: str | None = None
    http_status: int | None = None
    next_action: str | None = None
    details: Mapping[str, object] = field(default_factory=dict)

    def to_internal(self) -> dict[str, object]:
        """Return the full diagnostic presentation."""
        return {
            "code": self.code.value,
            "category": self.category.value,
            "message": self.message,
            "display_message": self.display_message,
            "retryable": self.retryable,
            "correlation_id": self.correlation_id,
            "http_status": self.http_status,
            "next_action": self.next_action,
            "details": dict(self.details),
        }

    def to_protocol(self) -> dict[str, object]:
        """Return the redacted wire presentation."""
        return {
            "code": self.code.value,
            "category": self.category.value,
            "message": self.display_message,
            "retryable": self.retryable,
            "correlation_id": self.correlation_id,
        }

    def to_display(self) -> NimbusDisplayError:
        """Return terminal/chat-friendly presentation data."""
        return NimbusDisplayError(
            title=_display_title(self.category),
            message=self.display_message,
            next_action=self.next_action,
        )


def _display_title(category: NimbusErrorCategory) -> str:
    """Return a short title for one error category."""
    return {
        NimbusErrorCategory.AUTH: "Authentication Required",
        NimbusErrorCategory.CONFIG: "Configuration Needed",
        NimbusErrorCategory.VALIDATION: "Invalid Request",
        NimbusErrorCategory.POLICY: "Permission Required",
        NimbusErrorCategory.PROVIDER: "Provider Error",
        NimbusErrorCategory.STORAGE: "Storage Error",
        NimbusErrorCategory.TIMEOUT: "Timed Out",
        NimbusErrorCategory.RATE_LIMIT: "Rate Limited",
        NimbusErrorCategory.CONFLICT: "Request Conflict",
        NimbusErrorCategory.INTERNAL: "Nimbus Error",
    }[category]


def error_from_exception(  # noqa: PLR0913 - explicit views keep call sites clear.
    exc: Exception,
    *,
    code: NimbusErrorCode = NimbusErrorCode.INTERNAL,
    category: NimbusErrorCategory = NimbusErrorCategory.INTERNAL,
    display_message: str = "Nimbus hit an unexpected error.",
    retryable: bool = False,
    correlation_id: str | None = None,
    http_status: int | None = None,
    next_action: str | None = None,
) -> NimbusError:
    """Translate an exception into a stable Nimbus error object."""
    return NimbusError(
        code=code,
        category=category,
        message=f"{exc.__class__.__name__}: {exc}",
        display_message=display_message,
        retryable=retryable,
        correlation_id=correlation_id,
        http_status=http_status,
        next_action=next_action,
    )
