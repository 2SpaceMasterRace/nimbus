"""Unit tests for the storage admin tooling (legacy-key renames)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from nimbus_cli.storage_admin import (
    _GIB_5,
    IdMapping,
    LegacyKey,
    RenamePlan,
    _chunk_ranges,
    _multipart_copy,
    _server_side_rename,
    _single_part_copy,
    build_readable_key,
    load_mapping,
    parse_legacy_key,
    plan_renames,
)

pytestmark = pytest.mark.unit


class TestParseLegacyKey:
    def test_canonical_legacy_key_parses(self) -> None:
        key = "slack/T089A399PQT/C0B1XKBS5UP/F0B2P7UNGJH/VarshaXH.java"
        parsed = parse_legacy_key(key)
        assert parsed == LegacyKey(
            full=key,
            team_id="T089A399PQT",
            channel_id="C0B1XKBS5UP",
            file_id="F0B2P7UNGJH",
            filename="VarshaXH.java",
        )

    def test_group_channel_id_parses(self) -> None:
        parsed = parse_legacy_key("slack/T1/G2/F3/notes.txt")
        assert parsed is not None
        assert parsed.channel_id == "G2"

    def test_dm_channel_id_parses(self) -> None:
        parsed = parse_legacy_key("slack/T1/D2/F3/dm.txt")
        assert parsed is not None
        assert parsed.channel_id == "D2"

    def test_filename_with_subpath_keeps_full_remainder(self) -> None:
        parsed = parse_legacy_key("slack/T1/C2/F3/sub/dir/file.png")
        assert parsed is not None
        assert parsed.filename == "sub/dir/file.png"

    def test_already_readable_key_is_not_legacy(self) -> None:
        # A path that already uses readable names should not match.
        assert parse_legacy_key("slack/nimbus-team/general/F123/x.txt") is None

    def test_top_level_object_is_not_legacy(self) -> None:
        assert parse_legacy_key("AGENTS.md") is None

    def test_partial_path_is_not_legacy(self) -> None:
        assert parse_legacy_key("slack/T1/C2/F3/") is None  # no filename


class TestBuildReadableKey:
    def _legacy(self) -> LegacyKey:
        return LegacyKey(
            full="slack/T089A399PQT/C0B1XKBS5UP/F0B2P7UNGJH/VarshaXH.java",
            team_id="T089A399PQT",
            channel_id="C0B1XKBS5UP",
            file_id="F0B2P7UNGJH",
            filename="VarshaXH.java",
        )

    def test_full_mapping_produces_readable_key(self) -> None:
        mapping = IdMapping(
            teams={"T089A399PQT": "nimbus-team"},
            channels={"C0B1XKBS5UP": "general"},
        )
        plan = build_readable_key(self._legacy(), mapping)
        assert plan.reason == "ok"
        assert plan.destination == (
            "slack/nimbus-team/general/F0B2P7UNGJH/VarshaXH.java"
        )

    def test_missing_team_blocks_rename(self) -> None:
        mapping = IdMapping(teams={}, channels={"C0B1XKBS5UP": "general"})
        plan = build_readable_key(self._legacy(), mapping)
        assert plan.reason == "no_team_in_mapping"
        assert plan.destination == plan.source

    def test_missing_channel_blocks_rename(self) -> None:
        mapping = IdMapping(teams={"T089A399PQT": "nimbus-team"}, channels={})
        plan = build_readable_key(self._legacy(), mapping)
        assert plan.reason == "no_channel_in_mapping"
        assert plan.destination == plan.source

    def test_unsafe_characters_in_names_are_sanitised(self) -> None:
        legacy = LegacyKey(
            full="slack/T1/C1/F1/anything",
            team_id="T1",
            channel_id="C1",
            file_id="F1",
            filename="anything",
        )
        mapping = IdMapping(
            teams={"T1": "team/with\\slashes"},
            channels={"C1": "chan\nwith\rnewlines"},
        )
        plan = build_readable_key(legacy, mapping)
        # The destination must still have exactly 5 path segments
        # (slack / team / channel / file_id / filename); embedded slashes in
        # the source names must be collapsed so they don't add extra levels.
        expected_segments = 5
        assert len(plan.destination.split("/")) == expected_segments
        assert "\n" not in plan.destination
        assert "\r" not in plan.destination


class TestPlanRenames:
    def test_classifies_legacy_skipped_and_unrelated(self) -> None:
        keys = [
            "slack/T1/C1/F1/a.txt",  # actionable
            "slack/T1/C2/F2/b.txt",  # skipped — channel C2 not in mapping
            "slack/already-readable/general/F3/c.txt",  # not_legacy
            "AGENTS.md",  # not_legacy
        ]
        mapping = IdMapping(teams={"T1": "tm"}, channels={"C1": "general"})

        actionable, skipped, not_legacy = plan_renames(keys, mapping)

        assert [p.destination for p in actionable] == ["slack/tm/general/F1/a.txt"]
        assert skipped == ["slack/T1/C2/F2/b.txt"]
        assert set(not_legacy) == {
            "slack/already-readable/general/F3/c.txt",
            "AGENTS.md",
        }


class TestChunkRanges:
    def test_single_chunk_when_size_fits_one_part(self) -> None:
        assert _chunk_ranges(100, 200) == [(0, 99)]

    def test_exact_boundary_produces_two_chunks(self) -> None:
        assert _chunk_ranges(200, 100) == [(0, 99), (100, 199)]

    def test_remainder_chunk_is_smaller(self) -> None:
        ranges = _chunk_ranges(150, 100)
        assert ranges == [(0, 99), (100, 149)]

    def test_all_ranges_are_contiguous_and_cover_full_size(self) -> None:
        size = 1_234_567
        part_size = 100_000
        ranges = _chunk_ranges(size, part_size)
        assert ranges[0][0] == 0
        assert ranges[-1][1] == size - 1
        for i in range(len(ranges) - 1):
            assert ranges[i][1] + 1 == ranges[i + 1][0]

    def test_single_byte_object(self) -> None:
        assert _chunk_ranges(1) == [(0, 0)]


class TestSinglePartCopy:
    def _plan(self) -> RenamePlan:
        return RenamePlan(source="src/key", destination="dst/key", reason="ok")

    def test_calls_copy_object_without_kms(self) -> None:
        s3 = MagicMock()
        _single_part_copy(s3=s3, bucket="b", plan=self._plan(), kms_key_id=None)
        s3.copy_object.assert_called_once_with(
            Bucket="b",
            Key="dst/key",
            CopySource={"Bucket": "b", "Key": "src/key"},
            MetadataDirective="COPY",
        )

    def test_propagates_kms_key_when_present(self) -> None:
        s3 = MagicMock()
        _single_part_copy(
            s3=s3,
            bucket="b",
            plan=self._plan(),
            kms_key_id="arn:aws:kms:us-east-1:123:key/k1",
        )
        _, kwargs = s3.copy_object.call_args
        assert kwargs["ServerSideEncryption"] == "aws:kms"
        assert kwargs["SSEKMSKeyId"] == "arn:aws:kms:us-east-1:123:key/k1"

    def test_no_kms_fields_when_key_is_none(self) -> None:
        s3 = MagicMock()
        _single_part_copy(s3=s3, bucket="b", plan=self._plan(), kms_key_id=None)
        _, kwargs = s3.copy_object.call_args
        assert "ServerSideEncryption" not in kwargs
        assert "SSEKMSKeyId" not in kwargs


class TestMultipartCopy:
    def _plan(self) -> RenamePlan:
        return RenamePlan(source="src/big", destination="dst/big", reason="ok")

    def _make_s3(self) -> MagicMock:
        s3 = MagicMock()
        s3.create_multipart_upload.return_value = {"UploadId": "uid-123"}
        # return_value (not side_effect) so any number of parts is handled
        s3.upload_part_copy.return_value = {"CopyPartResult": {"ETag": '"etag-fixed"'}}
        return s3

    def test_completes_multipart_upload_for_large_object(self) -> None:
        part_size = 100 * 1024**2
        size = _GIB_5 + 1
        expected_parts = (size + part_size - 1) // part_size
        s3 = self._make_s3()
        _multipart_copy(
            s3=s3, bucket="b", plan=self._plan(), size=size, kms_key_id=None
        )
        s3.create_multipart_upload.assert_called_once()
        assert s3.upload_part_copy.call_count == expected_parts
        s3.complete_multipart_upload.assert_called_once()
        s3.abort_multipart_upload.assert_not_called()

    def test_propagates_kms_key_in_create_call(self) -> None:
        s3 = self._make_s3()
        _multipart_copy(
            s3=s3,
            bucket="b",
            plan=self._plan(),
            size=_GIB_5 + 1,
            kms_key_id="arn:aws:kms:us-east-1:123:key/k1",
        )
        _, create_kwargs = s3.create_multipart_upload.call_args
        assert create_kwargs["ServerSideEncryption"] == "aws:kms"
        assert create_kwargs["SSEKMSKeyId"] == "arn:aws:kms:us-east-1:123:key/k1"

    def test_aborts_upload_and_reraises_on_part_failure(self) -> None:
        s3 = MagicMock()
        s3.create_multipart_upload.return_value = {"UploadId": "uid-abort"}
        s3.upload_part_copy.side_effect = RuntimeError("S3 error")
        with pytest.raises(RuntimeError, match="S3 error"):
            _multipart_copy(
                s3=s3, bucket="b", plan=self._plan(), size=_GIB_5 + 1, kms_key_id=None
            )
        s3.abort_multipart_upload.assert_called_once_with(
            Bucket="b", Key="dst/big", UploadId="uid-abort"
        )
        s3.complete_multipart_upload.assert_not_called()

    def test_aborts_upload_on_complete_failure(self) -> None:
        s3 = self._make_s3()
        s3.complete_multipart_upload.side_effect = RuntimeError("network timeout")
        with pytest.raises(RuntimeError, match="network timeout"):
            _multipart_copy(
                s3=s3, bucket="b", plan=self._plan(), size=_GIB_5 + 1, kms_key_id=None
            )
        s3.abort_multipart_upload.assert_called_once()


class TestServerSideRename:
    def _plan(self, *, large: bool = False) -> RenamePlan:
        return RenamePlan(
            source="src/file" if not large else "src/bigfile",
            destination="dst/file" if not large else "dst/bigfile",
            reason="ok",
        )

    def test_small_object_uses_single_part_copy(self) -> None:
        s3 = MagicMock()
        s3.head_object.return_value = {"ContentLength": 1024, "SSEKMSKeyId": None}
        with (
            patch("nimbus_cli.storage_admin._single_part_copy") as mock_single,
            patch("nimbus_cli.storage_admin._multipart_copy") as mock_multi,
        ):
            _server_side_rename(s3=s3, bucket="b", plan=self._plan())
        mock_single.assert_called_once()
        mock_multi.assert_not_called()
        s3.delete_object.assert_called_once_with(Bucket="b", Key="src/file")

    def test_large_object_uses_multipart_copy(self) -> None:
        s3 = MagicMock()
        big_size = _GIB_5 + 1
        s3.head_object.return_value = {"ContentLength": big_size, "SSEKMSKeyId": None}
        with (
            patch("nimbus_cli.storage_admin._single_part_copy") as mock_single,
            patch("nimbus_cli.storage_admin._multipart_copy") as mock_multi,
        ):
            _server_side_rename(s3=s3, bucket="b", plan=self._plan(large=True))
        mock_multi.assert_called_once()
        mock_single.assert_not_called()
        s3.delete_object.assert_called_once_with(Bucket="b", Key="src/bigfile")

    def test_kms_key_threaded_from_head_to_copy(self) -> None:
        s3 = MagicMock()
        kms = "arn:aws:kms:us-east-1:123:key/k1"
        s3.head_object.return_value = {"ContentLength": 512, "SSEKMSKeyId": kms}
        with patch("nimbus_cli.storage_admin._single_part_copy") as mock_single:
            _server_side_rename(s3=s3, bucket="b", plan=self._plan())
        _, kwargs = mock_single.call_args
        assert kwargs["kms_key_id"] == kms

    def test_delete_not_called_if_copy_raises(self) -> None:
        s3 = MagicMock()
        s3.head_object.return_value = {"ContentLength": 100, "SSEKMSKeyId": None}
        with (
            patch(
                "nimbus_cli.storage_admin._single_part_copy",
                side_effect=RuntimeError("copy failed"),
            ),
            pytest.raises(RuntimeError, match="copy failed"),
        ):
            _server_side_rename(s3=s3, bucket="b", plan=self._plan())
        s3.delete_object.assert_not_called()


class TestLoadMapping:
    def test_loads_toml_mapping(self, tmp_path: Path) -> None:
        mapping_path = tmp_path / "map.toml"
        mapping_path.write_text(
            '[teams]\n"T1" = "nimbus-team"\n\n[channels]\n"C1" = "general"\n',
            encoding="utf-8",
        )
        mapping = load_mapping(mapping_path)
        assert mapping.teams == {"T1": "nimbus-team"}
        assert mapping.channels == {"C1": "general"}

    def test_loads_json_mapping(self, tmp_path: Path) -> None:
        mapping_path = tmp_path / "map.json"
        mapping_path.write_text(
            '{"teams": {"T1": "nimbus-team"}, "channels": {"C1": "general"}}',
            encoding="utf-8",
        )
        mapping = load_mapping(mapping_path)
        assert mapping.teams == {"T1": "nimbus-team"}

    def test_rejects_unsupported_extension(self, tmp_path: Path) -> None:
        mapping_path = tmp_path / "map.yaml"
        mapping_path.write_text("teams: {}", encoding="utf-8")
        with pytest.raises(ValueError, match="unsupported mapping format"):
            load_mapping(mapping_path)

    def test_rejects_malformed_payload(self, tmp_path: Path) -> None:
        mapping_path = tmp_path / "bad.json"
        mapping_path.write_text('{"teams": "not a dict"}', encoding="utf-8")
        with pytest.raises(ValueError, match="must contain 'teams' and 'channels'"):
            load_mapping(mapping_path)
