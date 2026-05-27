"""Provider-level storage capability protocols.

This module describes optional cloud-provider behavior without binding Nimbus
runtime code to AWS, a generated client, or a concrete adapter.  The protocols
are intentionally small: callers can check for one capability at a time and
fall back to the base ``CloudStorageClient`` contract when it is absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

    from cloud_storage_api import DeleteResult, ObjectInfo


class ProviderCapability(StrEnum):
    """Named optional provider features Nimbus can discover and branch on."""

    PAGINATION = "pagination"
    READ_BYTES = "read_bytes"
    RANGE_READ = "range_read"
    COPY = "copy"
    DELETE = "delete"
    CHECKSUM = "checksum"
    VERSION = "version"
    RESTORE = "restore"
    CAPABILITY_DISCOVERY = "capability_discovery"


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """Declared or discovered capabilities for one storage provider."""

    provider_name: str
    capabilities: frozenset[ProviderCapability]

    def supports(self, capability: ProviderCapability) -> bool:
        """Return whether this provider supports ``capability``."""
        return capability in self.capabilities


@dataclass(frozen=True, slots=True)
class ObjectChecksum:
    """Provider-neutral checksum metadata for one object version."""

    object_name: str
    algorithm: str
    value: str
    version_id: str | None = None


@dataclass(frozen=True, slots=True)
class ObjectVersion:
    """Provider-neutral metadata for one stored object version."""

    object_name: str
    version_id: str
    is_latest: bool
    is_delete_marker: bool = False
    size_bytes: int | None = None
    updated_at: datetime | None = None
    integrity: str | None = None


@dataclass(frozen=True, slots=True)
class ObjectRestoreResult:
    """Result of making a previous object version current again."""

    object_name: str
    source_version_id: str
    restored_version_id: str | None
    restored: bool


@runtime_checkable
class ProviderPagination(Protocol):
    """Capability for bounded, token-based object listing."""

    def list_files_page(
        self,
        container: str,
        prefix: str,
        max_keys: int,
        continuation_token: str = "",
    ) -> tuple[list[ObjectInfo], str]:
        """Return one deterministic page and an empty token when exhausted."""
        ...


@runtime_checkable
class ProviderByteReader(Protocol):
    """Capability for reading an entire object directly as bytes."""

    def read_object(self, container: str, key: str) -> bytes:
        """Return the bytes stored at ``container/key``."""
        ...


@runtime_checkable
class ProviderRangeReader(Protocol):
    """Capability for reading an inclusive byte range without temp files."""

    def get_object_range(
        self,
        container: str,
        key: str,
        start: int,
        end: int,
    ) -> tuple[bytes, int]:
        """Return ``(content, total_size)`` for the inclusive byte range."""
        ...


@runtime_checkable
class ProviderCopier(Protocol):
    """Capability for provider-side object copy."""

    def copy_object(
        self,
        src_container: str,
        src_key: str,
        dst_container: str,
        dst_key: str,
    ) -> ObjectInfo:
        """Copy one object to a destination object and return destination info."""
        ...


@runtime_checkable
class ProviderDeleter(Protocol):
    """Capability for deleting an object through the provider contract."""

    def delete_file(self, container: str, object_name: str) -> DeleteResult:
        """Delete one object and return provider-neutral delete metadata."""
        ...


@runtime_checkable
class ProviderChecksumReader(Protocol):
    """Capability for retrieving provider checksum metadata."""

    def get_object_checksum(
        self,
        container: str,
        key: str,
        version_id: str | None = None,
    ) -> ObjectChecksum:
        """Return checksum metadata for the current or requested version."""
        ...


@runtime_checkable
class ProviderVersionLister(Protocol):
    """Capability for listing provider object versions."""

    def list_object_versions(
        self,
        container: str,
        key: str,
    ) -> tuple[ObjectVersion, ...]:
        """Return versions for one object, newest first."""
        ...


@runtime_checkable
class ProviderVersionRestorer(Protocol):
    """Capability for restoring a previous provider object version."""

    def restore_object_version(
        self,
        container: str,
        key: str,
        version_id: str,
    ) -> ObjectRestoreResult:
        """Make ``version_id`` current again for ``container/key``."""
        ...


@runtime_checkable
class ProviderCapabilityDiscovery(Protocol):
    """Capability for explicit provider self-description."""

    def provider_capabilities(self) -> ProviderCapabilities:
        """Return the provider's declared optional feature set."""
        ...


_STRUCTURAL_CAPABILITIES: tuple[tuple[ProviderCapability, type[object]], ...] = (
    (ProviderCapability.PAGINATION, ProviderPagination),
    (ProviderCapability.READ_BYTES, ProviderByteReader),
    (ProviderCapability.RANGE_READ, ProviderRangeReader),
    (ProviderCapability.COPY, ProviderCopier),
    (ProviderCapability.DELETE, ProviderDeleter),
    (ProviderCapability.CHECKSUM, ProviderChecksumReader),
    (ProviderCapability.VERSION, ProviderVersionLister),
    (ProviderCapability.RESTORE, ProviderVersionRestorer),
    (ProviderCapability.CAPABILITY_DISCOVERY, ProviderCapabilityDiscovery),
)


def discover_provider_capabilities(provider: object) -> ProviderCapabilities:
    """Return declared and structurally visible capabilities for ``provider``."""
    structural = frozenset(
        capability
        for capability, protocol in _STRUCTURAL_CAPABILITIES
        if isinstance(provider, protocol)
    )
    if isinstance(provider, ProviderCapabilityDiscovery):
        declared = provider.provider_capabilities()
        return ProviderCapabilities(
            provider_name=declared.provider_name,
            capabilities=declared.capabilities | structural,
        )
    return ProviderCapabilities(
        provider_name=provider.__class__.__name__,
        capabilities=structural,
    )


__all__ = [
    "ObjectChecksum",
    "ObjectRestoreResult",
    "ObjectVersion",
    "ProviderByteReader",
    "ProviderCapabilities",
    "ProviderCapability",
    "ProviderCapabilityDiscovery",
    "ProviderChecksumReader",
    "ProviderCopier",
    "ProviderDeleter",
    "ProviderPagination",
    "ProviderRangeReader",
    "ProviderVersionLister",
    "ProviderVersionRestorer",
    "discover_provider_capabilities",
]
