"""Unit tests for small helper functions in nimbus_runtime.runtime."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from nimbus_runtime.domain import (
    DeleteReport,
    ManifestReport,
    ObjectVerificationEntry,
    ObjectVerificationReport,
    RestorePlan,
    RestoreStrategy,
    StorageMutationReport,
    UploadReport,
)
from nimbus_runtime.runtime import (
    _ai_error_kind,
    _artifact_payload_to_summary,
    _object_info_size_bytes,
    _object_info_value,
    _RestoreSourceEvidence,
    _strip_wrapping_quotes,
    _validate_session_id,
)

from ai_client_api import (
    AIClientConfigError,
    AIClientError,
    AIProviderError,
    AIRateLimitError,
    AIStepBudgetExceededError,
    AITimeoutError,
)

pytestmark = pytest.mark.unit


class TestValidateSessionId:
    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="contains unsafe characters"):
            _validate_session_id("")

    def test_unsafe_chars_raises(self) -> None:
        with pytest.raises(ValueError, match="contains unsafe characters"):
            _validate_session_id("../etc/passwd")

    def test_safe_chars_ok(self) -> None:
        _validate_session_id("abc-123.def:456")  # should not raise


class TestRestoreSourceEvidenceAvailable:
    def test_available_true(self) -> None:
        evidence = _RestoreSourceEvidence(
            version_id="v1", size_bytes=100, sha256_hex="abc"
        )
        assert evidence.available is True

    def test_available_false(self) -> None:
        evidence = _RestoreSourceEvidence(
            version_id=None,
            size_bytes=None,
            sha256_hex=None,
            unavailable_reason="no metadata",
        )
        assert evidence.available is False


class TestAiErrorKind:
    def test_config_error(self) -> None:
        assert _ai_error_kind(AIClientConfigError("bad config")) == "config_error"

    def test_rate_limit(self) -> None:
        assert _ai_error_kind(AIRateLimitError("rate limited")) == "rate_limit"

    def test_timeout(self) -> None:
        assert _ai_error_kind(AITimeoutError("timeout")) == "timeout"

    def test_step_budget_exceeded(self) -> None:
        assert (
            _ai_error_kind(AIStepBudgetExceededError("too many steps"))
            == "step_budget_exceeded"
        )

    def test_provider_error(self) -> None:
        assert _ai_error_kind(AIProviderError("provider down")) == "provider_error"

    def test_client_error_fallback(self) -> None:
        assert _ai_error_kind(AIClientError("generic")) == "client_error"

    def test_unknown_error(self) -> None:
        assert _ai_error_kind(ValueError("unknown")) == "valueerror"


class TestStripWrappingQuotes:
    def test_double_quotes(self) -> None:
        assert _strip_wrapping_quotes('"hello"') == "hello"

    def test_single_quotes(self) -> None:
        assert _strip_wrapping_quotes("'hello'") == "hello"

    def test_no_quotes(self) -> None:
        assert _strip_wrapping_quotes("hello") == "hello"

    def test_backtick_stripped(self) -> None:
        assert _strip_wrapping_quotes("`hello`") == "hello"

    def test_short_text_not_stripped(self) -> None:
        assert _strip_wrapping_quotes('"') == '"'


class TestObjectInfoValue:
    def test_mapping_object(self) -> None:
        assert _object_info_value({"size_bytes": 42}, "size_bytes") == 42

    def test_attribute_object(self) -> None:
        class FakeInfo:
            size_bytes = 100

        assert _object_info_value(FakeInfo(), "size_bytes") == 100

    def test_missing_key(self) -> None:
        assert _object_info_value({}, "missing") is None


class TestObjectInfoSizeBytes:
    def test_valid_int(self) -> None:
        assert _object_info_size_bytes({"size_bytes": 42}) == 42

    def test_bool_returns_none(self) -> None:
        assert _object_info_size_bytes({"size_bytes": True}) is None

    def test_negative_returns_none(self) -> None:
        assert _object_info_size_bytes({"size_bytes": -1}) is None

    def test_missing_returns_none(self) -> None:
        assert _object_info_size_bytes({}) is None


class TestArtifactPayloadToSummary:
    def test_delete_report(self) -> None:
        plan = RestorePlan(
            original_key="files/doc.txt",
            strategy=RestoreStrategy.TRASH_COPY,
            restorable=True,
            trash_key=".trash/doc.txt",
            version_id="v1",
            sha256_hex="abc",
            size_bytes=100,
            deleted_by="test",
            deleted_at=datetime(2026, 1, 1, tzinfo=UTC),
            restore_command=None,
            limitations=(),
        )
        report = DeleteReport(
            remote_path="files/doc.txt",
            deleted=True,
            version_id="v1",
            restore_plan=plan,
        )
        summary = _artifact_payload_to_summary(report)
        assert summary["remote_path"] == "files/doc.txt"
        assert summary["deleted"] is True

    def test_upload_report(self) -> None:
        report = UploadReport(
            remote_path="files/photo.jpg",
            filename="photo.jpg",
            size_bytes=1024,
            sha256_hex="def",
        )
        summary = _artifact_payload_to_summary(report)
        assert summary["size_bytes"] == 1024

    def test_storage_mutation_report(self) -> None:
        report = StorageMutationReport(
            operation="copy",
            source_path="src/key",
            dest_path="dst/key",
            remote_path="dst/key",
            size_bytes=512,
            sha256_hex="ghi",
            overwrote=False,
            source_deleted=False,
            dest_version_id="v2",
            verified=True,
            verifier="backup",
        )
        summary = _artifact_payload_to_summary(report)
        assert summary["operation"] == "copy"

    def test_object_verification_report(self) -> None:
        entry = ObjectVerificationEntry(
            file_id="f1",
            object_key="files/doc.txt",
            size_bytes=100,
            sha256_hex="abc",
            verified=True,
        )
        report = ObjectVerificationReport(
            verifier="drift_verifier",
            subject="backup-2026-01",
            verified=True,
            entries=(entry,),
            reason=None,
        )
        summary = _artifact_payload_to_summary(report)
        assert summary["verifier"] == "drift_verifier"
        assert summary["verified"] is True
        assert summary["entry_count"] == 1
        assert summary["failed_count"] == 0

    def test_manifest_report(self) -> None:
        from nimbus_runtime.domain import ManifestObjectEntry

        report = ManifestReport(
            source_platform="slack",
            workspace_id="T123",
            channel_id="C456",
            destination_container="nimbus-backups",
            destination_prefix="backups/T123/C456",
            scanned_count=10,
            matched_count=5,
            total_count=10,
            truncated=False,
            object_entries=(
                ManifestObjectEntry(
                    file_id="f1",
                    name="doc.txt",
                    object_key="files/doc.txt",
                    size_bytes=100,
                    sha256_hex="abc",
                    disposition="saved",
                ),
                ManifestObjectEntry(
                    file_id="f2",
                    name="note.txt",
                    object_key="files/note.txt",
                    size_bytes=200,
                    sha256_hex="def",
                    disposition="deduped",
                    deduped_from_key="files/doc.txt",
                ),
            ),
            failed_files=(),
            verifier_artifact_id="art-1",
        )
        summary = _artifact_payload_to_summary(report)
        assert summary["source_platform"] == "slack"
        assert summary["saved_count"] == 2
        assert summary["failed_count"] == 0

    def test_unsupported_type_raises(self) -> None:
        with pytest.raises(TypeError, match="unsupported artifact payload"):
            _artifact_payload_to_summary("not-a-payload")  # type: ignore[arg-type]


class TestObjectVerificationFailedCount:
    def test_some_entries_failed(self) -> None:
        entries = (
            ObjectVerificationEntry(
                file_id="f1",
                object_key="files/a.txt",
                size_bytes=100,
                sha256_hex="a",
                verified=True,
            ),
            ObjectVerificationEntry(
                file_id="f2",
                object_key="files/b.txt",
                size_bytes=200,
                sha256_hex="b",
                verified=False,
                reason="mismatch",
            ),
            ObjectVerificationEntry(
                file_id="f3",
                object_key="files/c.txt",
                size_bytes=300,
                sha256_hex="c",
                verified=False,
                reason="missing",
            ),
        )
        report = ObjectVerificationReport(
            verifier="drift",
            subject="test",
            verified=False,
            entries=entries,
            reason="2 files missing",
        )
        summary = _artifact_payload_to_summary(report)
        assert summary["failed_count"] == 2
        assert summary["entry_count"] == 3
        assert summary["reason"] == "2 files missing"
