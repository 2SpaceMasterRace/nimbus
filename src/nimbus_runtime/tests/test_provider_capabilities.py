"""Contract tests for provider-level storage capability protocols."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import pytest
from cloud_storage_api import DeleteResult, ObjectInfo, ObjectNotFoundError
from nimbus_runtime.provider_capabilities import (
    ObjectChecksum,
    ObjectRestoreResult,
    ObjectVersion,
    ProviderByteReader,
    ProviderCapabilities,
    ProviderCapability,
    ProviderCapabilityDiscovery,
    ProviderChecksumReader,
    ProviderCopier,
    ProviderDeleter,
    ProviderPagination,
    ProviderRangeReader,
    ProviderVersionLister,
    ProviderVersionRestorer,
    discover_provider_capabilities,
)

pytestmark = pytest.mark.unit


@dataclass(frozen=True, slots=True)
class _StoredVersion:
    version_id: str
    body: bytes
    is_delete_marker: bool = False


@dataclass(slots=True)
class _FakeSecondProvider:
    _versions: dict[tuple[str, str], list[_StoredVersion]] = field(default_factory=dict)
    _next_id: int = 0

    def provider_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            provider_name="fake-second-cloud",
            capabilities=frozenset(ProviderCapability),
        )

    def seed_object(self, container: str, key: str, body: bytes) -> str:
        return self._write_version(container, key, body, is_delete_marker=False)

    def list_files_page(
        self,
        container: str,
        prefix: str,
        max_keys: int,
        continuation_token: str = "",
    ) -> tuple[list[ObjectInfo], str]:
        if max_keys <= 0:
            msg = "max_keys must be positive"
            raise ValueError(msg)
        start = int(continuation_token) if continuation_token else 0
        current_keys = sorted(
            key
            for stored_container, key in self._versions
            if stored_container == container
            and key.startswith(prefix)
            and not self._latest(container, key).is_delete_marker
        )
        page_keys = current_keys[start : start + max_keys]
        next_start = start + len(page_keys)
        next_token = str(next_start) if next_start < len(current_keys) else ""
        return [self.get_file_info(container, key) for key in page_keys], next_token

    def get_file_info(self, container: str, object_name: str) -> ObjectInfo:
        version = self._current(container, object_name)
        return ObjectInfo(
            object_name=object_name,
            version_id=version.version_id,
            size_bytes=len(version.body),
            integrity=self._sha256(version.body),
            metadata={"provider": "fake-second-cloud"},
        )

    def read_object(self, container: str, key: str) -> bytes:
        return self._current(container, key).body

    def get_object_range(
        self,
        container: str,
        key: str,
        start: int,
        end: int,
    ) -> tuple[bytes, int]:
        if start < 0 or end < start:
            msg = "byte range must be non-negative and inclusive"
            raise ValueError(msg)
        body = self.read_object(container, key)
        return body[start : end + 1], len(body)

    def copy_object(
        self,
        src_container: str,
        src_key: str,
        dst_container: str,
        dst_key: str,
    ) -> ObjectInfo:
        body = self.read_object(src_container, src_key)
        self._write_version(dst_container, dst_key, body, is_delete_marker=False)
        return self.get_file_info(dst_container, dst_key)

    def delete_file(self, container: str, object_name: str) -> DeleteResult:
        self._current(container, object_name)
        version_id = self._write_version(
            container,
            object_name,
            b"",
            is_delete_marker=True,
        )
        return DeleteResult(
            deleted=True,
            version_id=version_id,
            request_charged=None,
        )

    def get_object_checksum(
        self,
        container: str,
        key: str,
        version_id: str | None = None,
    ) -> ObjectChecksum:
        version = self._version(container, key, version_id)
        if version.is_delete_marker:
            msg = f"{container}/{key} version {version.version_id} is a delete marker"
            raise ObjectNotFoundError(msg)
        return ObjectChecksum(
            object_name=key,
            algorithm="sha256",
            value=self._sha256(version.body),
            version_id=version.version_id,
        )

    def list_object_versions(
        self,
        container: str,
        key: str,
    ) -> tuple[ObjectVersion, ...]:
        versions = self._versions.get((container, key), [])
        return tuple(
            ObjectVersion(
                object_name=key,
                version_id=version.version_id,
                is_latest=index == 0,
                is_delete_marker=version.is_delete_marker,
                size_bytes=None if version.is_delete_marker else len(version.body),
                integrity=None
                if version.is_delete_marker
                else self._sha256(version.body),
            )
            for index, version in enumerate(reversed(versions))
        )

    def restore_object_version(
        self,
        container: str,
        key: str,
        version_id: str,
    ) -> ObjectRestoreResult:
        version = self._version(container, key, version_id)
        if version.is_delete_marker:
            msg = f"{container}/{key} version {version_id} is a delete marker"
            raise ObjectNotFoundError(msg)
        restored_version_id = self._write_version(
            container,
            key,
            version.body,
            is_delete_marker=False,
        )
        return ObjectRestoreResult(
            object_name=key,
            source_version_id=version_id,
            restored_version_id=restored_version_id,
            restored=True,
        )

    def _write_version(
        self,
        container: str,
        key: str,
        body: bytes,
        *,
        is_delete_marker: bool,
    ) -> str:
        self._next_id += 1
        version_id = f"fake-v{self._next_id:04d}"
        self._versions.setdefault((container, key), []).append(
            _StoredVersion(
                version_id=version_id,
                body=body,
                is_delete_marker=is_delete_marker,
            )
        )
        return version_id

    def _latest(self, container: str, key: str) -> _StoredVersion:
        versions = self._versions.get((container, key))
        if not versions:
            msg = f"{container}/{key} not found"
            raise ObjectNotFoundError(msg)
        return versions[-1]

    def _current(self, container: str, key: str) -> _StoredVersion:
        version = self._latest(container, key)
        if version.is_delete_marker:
            msg = f"{container}/{key} was deleted"
            raise ObjectNotFoundError(msg)
        return version

    def _version(
        self,
        container: str,
        key: str,
        version_id: str | None,
    ) -> _StoredVersion:
        if version_id is None:
            return self._current(container, key)
        for version in self._versions.get((container, key), ()):
            if version.version_id == version_id:
                return version
        msg = f"{container}/{key} version {version_id} not found"
        raise ObjectNotFoundError(msg)

    @staticmethod
    def _sha256(body: bytes) -> str:
        return hashlib.sha256(body).hexdigest()


def test_fake_second_provider_satisfies_all_capability_protocols() -> None:
    provider = _FakeSecondProvider()

    assert isinstance(provider, ProviderPagination)
    assert isinstance(provider, ProviderByteReader)
    assert isinstance(provider, ProviderRangeReader)
    assert isinstance(provider, ProviderCopier)
    assert isinstance(provider, ProviderDeleter)
    assert isinstance(provider, ProviderChecksumReader)
    assert isinstance(provider, ProviderVersionLister)
    assert isinstance(provider, ProviderVersionRestorer)
    assert isinstance(provider, ProviderCapabilityDiscovery)

    discovered = discover_provider_capabilities(provider)
    assert discovered.provider_name == "fake-second-cloud"
    assert all(discovered.supports(capability) for capability in ProviderCapability)


def test_pagination_contract_is_token_based_and_deterministic() -> None:
    provider = _FakeSecondProvider()
    provider.seed_object("vault", "docs/b.txt", b"bravo")
    provider.seed_object("vault", "docs/a.txt", b"alpha")
    provider.seed_object("vault", "images/logo.png", b"png")
    provider.seed_object("vault", "docs/c.txt", b"charlie")

    first_page, token = provider.list_files_page("vault", "docs/", 2)
    repeated_first_page, repeated_token = provider.list_files_page("vault", "docs/", 2)
    second_page, final_token = provider.list_files_page("vault", "docs/", 2, token)

    assert [item.object_name for item in first_page] == ["docs/a.txt", "docs/b.txt"]
    assert token == "2"
    assert [item.object_name for item in repeated_first_page] == [
        "docs/a.txt",
        "docs/b.txt",
    ]
    assert repeated_token == token
    assert [item.object_name for item in second_page] == ["docs/c.txt"]
    assert final_token == ""


def test_read_bytes_and_range_read_contracts_return_exact_bytes() -> None:
    provider = _FakeSecondProvider()
    provider.seed_object("vault", "docs/readme.txt", b"hello multi-cloud")

    assert provider.read_object("vault", "docs/readme.txt") == b"hello multi-cloud"
    content, total_size = provider.get_object_range("vault", "docs/readme.txt", 6, 10)

    assert content == b"multi"
    assert total_size == len(b"hello multi-cloud")

    with pytest.raises(ValueError, match="byte range"):
        provider.get_object_range("vault", "docs/readme.txt", 5, 4)


def test_copy_delete_and_checksum_contracts_are_provider_neutral() -> None:
    provider = _FakeSecondProvider()
    provider.seed_object("vault", "docs/source.txt", b"copy me")

    copied = provider.copy_object(
        "vault",
        "docs/source.txt",
        "vault",
        "archive/source.txt",
    )
    source_checksum = provider.get_object_checksum("vault", "docs/source.txt")
    copied_checksum = provider.get_object_checksum("vault", "archive/source.txt")
    delete_result = provider.delete_file("vault", "archive/source.txt")

    assert copied.object_name == "archive/source.txt"
    assert source_checksum.algorithm == "sha256"
    assert copied_checksum.value == source_checksum.value
    assert delete_result == {
        "deleted": True,
        "version_id": "fake-v0003",
        "request_charged": None,
    }
    with pytest.raises(ObjectNotFoundError):
        provider.read_object("vault", "archive/source.txt")
    assert provider.read_object("vault", "docs/source.txt") == b"copy me"


def test_version_listing_and_restore_contracts_preserve_history() -> None:
    provider = _FakeSecondProvider()
    first_version = provider.seed_object("vault", "docs/history.txt", b"first")
    second_version = provider.seed_object("vault", "docs/history.txt", b"second")
    provider.delete_file("vault", "docs/history.txt")

    versions_after_delete = provider.list_object_versions("vault", "docs/history.txt")
    restore_result = provider.restore_object_version(
        "vault",
        "docs/history.txt",
        first_version,
    )
    versions_after_restore = provider.list_object_versions("vault", "docs/history.txt")
    second_checksum = provider.get_object_checksum(
        "vault",
        "docs/history.txt",
        version_id=second_version,
    )

    assert [version.version_id for version in versions_after_delete] == [
        "fake-v0003",
        second_version,
        first_version,
    ]
    assert versions_after_delete[0].is_delete_marker is True
    assert restore_result == ObjectRestoreResult(
        object_name="docs/history.txt",
        source_version_id=first_version,
        restored_version_id="fake-v0004",
        restored=True,
    )
    assert provider.read_object("vault", "docs/history.txt") == b"first"
    assert versions_after_restore[0].version_id == "fake-v0004"
    assert versions_after_restore[0].is_latest is True
    assert second_checksum.value == hashlib.sha256(b"second").hexdigest()
