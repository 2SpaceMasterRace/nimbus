"""Unit tests for the Nimbus search and knowledge projection."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from nimbus_runtime import (
    FileSearchIndexStore,
    SearchActorScope,
    SearchChunk,
    SearchDocument,
    SearchDocumentStatus,
    SearchFilters,
    SearchQuery,
    TenantIdentity,
    VerifiedActor,
)

pytestmark = pytest.mark.unit


def _tenant(workspace_id: str = "T123TEAM") -> TenantIdentity:
    return TenantIdentity(platform="slack", workspace_id=workspace_id)


def _actor(
    tenant: TenantIdentity,
    *,
    user_id: str = "U123USER",
) -> VerifiedActor:
    return VerifiedActor(
        tenant=tenant,
        user_id=user_id,
        auth_source="slack_signed_event",
        bridge_id="slack",
        verified_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _document(
    *,
    tenant: TenantIdentity,
    document_id: str,
    channel_id: str = "C123CHAN",
    actor_ids: tuple[str, ...] = (),
    channel_ids: tuple[str, ...] | None = None,
    status: SearchDocumentStatus = SearchDocumentStatus.SEARCHABLE,
    title: str = "Design note",
    saved: bool | None = False,
    extraction_error: str | None = None,
) -> SearchDocument:
    visible_channels = (channel_id,) if channel_ids is None else channel_ids
    return SearchDocument(
        tenant=tenant,
        document_id=document_id,
        source_uri=f"slack://{tenant.workspace_id}/{channel_id}/{document_id}",
        object_key=f"channels/{channel_id}/{document_id}.txt",
        title=title,
        content_type="text/plain",
        size_bytes=128,
        status=status,
        workspace_id=tenant.workspace_id,
        channel_id=channel_id,
        uploader_id="UUPLOAD",
        bucket="demo-bucket",
        saved=saved,
        sha256_hex=f"{document_id:0<64}"[:64],
        source_created_at=datetime(2026, 1, 2, tzinfo=UTC),
        indexed_at=datetime(2026, 1, 3, tzinfo=UTC),
        extraction_error=extraction_error,
        visible_to_actor_ids=actor_ids,
        visible_to_channel_ids=visible_channels,
        metadata={"source": "unit-test"},
    )


def _chunk(
    *,
    tenant: TenantIdentity,
    document_id: str,
    chunk_id: str,
    text: str,
    chunk_index: int = 0,
) -> SearchChunk:
    return SearchChunk(
        tenant=tenant,
        document_id=document_id,
        chunk_id=chunk_id,
        chunk_index=chunk_index,
        text=text,
        page_number=1,
        start_offset=0,
        end_offset=len(text),
    )


def test_file_search_filters_acl_before_scoring(tmp_path: Path) -> None:
    """Invisible high-scoring chunks must not outrank visible documents."""
    tenant = _tenant()
    store = FileSearchIndexStore(tmp_path)
    visible = _document(tenant=tenant, document_id="doc-visible", channel_id="COPEN")
    secret = _document(tenant=tenant, document_id="doc-secret", channel_id="CSECRET")
    other_tenant = _tenant("T999TEAM")
    cross_tenant = _document(
        tenant=other_tenant,
        document_id="doc-cross-tenant",
        channel_id="COPEN",
    )

    store.index_document(
        document=visible,
        chunks=(
            _chunk(
                tenant=tenant,
                document_id=visible.document_id,
                chunk_id="chunk-visible",
                text="Acme appears once in the visible design note.",
            ),
        ),
    )
    store.index_document(
        document=secret,
        chunks=(
            _chunk(
                tenant=tenant,
                document_id=secret.document_id,
                chunk_id="chunk-secret",
                text="Acme Acme Acme Acme Acme confidential roadmap.",
            ),
        ),
    )
    store.index_document(
        document=cross_tenant,
        chunks=(
            _chunk(
                tenant=other_tenant,
                document_id=cross_tenant.document_id,
                chunk_id="chunk-cross",
                text="Acme from another tenant must never leak.",
            ),
        ),
    )

    results = store.search(
        scope=SearchActorScope(
            actor=_actor(tenant),
            visible_channel_ids=frozenset({"COPEN"}),
        ),
        query=SearchQuery(text="Acme", filters=SearchFilters(saved=False)),
    )

    assert [result.document.document_id for result in results] == ["doc-visible"]
    assert results[0].citations == (
        "slack://T123TEAM/COPEN/doc-visible:page:1:chunk:0",
    )
    assert "untrusted_extracted_text" in results[0].content_warnings


def test_file_search_allows_direct_actor_acl_without_channel_scope(
    tmp_path: Path,
) -> None:
    """Actor-specific ACL grants should work without channel visibility."""
    tenant = _tenant()
    actor = _actor(tenant)
    store = FileSearchIndexStore(tmp_path)
    document = _document(
        tenant=tenant,
        document_id="doc-direct",
        channel_id="CPRIVATE",
        actor_ids=(actor.user_id,),
        channel_ids=(),
    )
    store.index_document(
        document=document,
        chunks=(
            _chunk(
                tenant=tenant,
                document_id=document.document_id,
                chunk_id="chunk-direct",
                text="The Acme contract is shared directly with one actor.",
            ),
        ),
    )

    results = store.search(
        scope=SearchActorScope(actor=actor),
        query=SearchQuery(text="Acme"),
    )

    assert [result.document.document_id for result in results] == ["doc-direct"]


def test_file_search_records_extraction_failures_as_metadata_only_results(
    tmp_path: Path,
) -> None:
    """Malformed files should be searchable by state without fake text hits."""
    tenant = _tenant()
    store = FileSearchIndexStore(tmp_path)
    document = _document(
        tenant=tenant,
        document_id="doc-malformed",
        status=SearchDocumentStatus.EXTRACTION_FAILED,
        title="malformed Acme archive",
        extraction_error="zip entry limit exceeded",
    )
    store.index_document(document=document, chunks=())
    scope = SearchActorScope(
        actor=_actor(tenant),
        visible_channel_ids=frozenset({"C123CHAN"}),
    )

    metadata_results = store.search(
        scope=scope,
        query=SearchQuery(
            filters=SearchFilters(status=SearchDocumentStatus.EXTRACTION_FAILED),
        ),
    )
    text_results = store.search(scope=scope, query=SearchQuery(text="Acme"))

    assert [result.document.document_id for result in metadata_results] == [
        "doc-malformed"
    ]
    assert metadata_results[0].document.extraction_error == "zip entry limit exceeded"
    assert metadata_results[0].chunk_hits == ()
    assert text_results == ()


def test_file_search_rejects_cross_document_chunks(tmp_path: Path) -> None:
    """Chunk ownership must match the indexed document."""
    tenant = _tenant()
    store = FileSearchIndexStore(tmp_path)
    document = _document(tenant=tenant, document_id="doc-owner")

    with pytest.raises(ValueError, match="chunk document_id"):
        store.index_document(
            document=document,
            chunks=(
                _chunk(
                    tenant=tenant,
                    document_id="doc-other",
                    chunk_id="chunk-other",
                    text="Acme",
                ),
            ),
        )


# -- validation tests for SearchDocument, SearchChunk, SearchFilters, SearchQuery --


class TestSearchDocumentValidation:
    def test_negative_size_bytes(self) -> None:
        tenant = _tenant()
        with pytest.raises(ValueError, match="size_bytes cannot be negative"):
            SearchDocument(
                tenant=tenant,
                document_id="doc-1",
                source_uri="slack://T1/C1/doc-1",
                object_key="channels/C1/doc-1.txt",
                title="test",
                content_type="text/plain",
                size_bytes=-1,
                status=SearchDocumentStatus.SEARCHABLE,
            )

    def test_effective_channel_acl_falls_back_to_channel_id(self) -> None:
        tenant = _tenant()
        doc = SearchDocument(
            tenant=tenant,
            document_id="doc-acl",
            source_uri="slack://T1/C1/doc-acl",
            object_key="channels/C1/doc-acl.txt",
            title="acl test",
            content_type="text/plain",
            size_bytes=100,
            status=SearchDocumentStatus.SEARCHABLE,
            workspace_id=tenant.workspace_id,
            channel_id=None,
            visible_to_channel_ids=(),
        )
        assert doc.effective_channel_acl == frozenset()

    def test_effective_channel_acl_from_visible(self) -> None:
        tenant = _tenant()
        doc = SearchDocument(
            tenant=tenant,
            document_id="doc-acl-2",
            source_uri="slack://T1/C1/doc-acl-2",
            object_key="channels/C1/doc-acl-2.txt",
            title="acl test 2",
            content_type="text/plain",
            size_bytes=100,
            status=SearchDocumentStatus.SEARCHABLE,
            workspace_id=tenant.workspace_id,
            channel_id="COPEN",
            visible_to_channel_ids=("CSPECIAL",),
        )
        assert doc.effective_channel_acl == frozenset({"CSPECIAL"})


class TestSearchChunkValidation:
    def test_chunk_index_negative(self) -> None:
        tenant = _tenant()
        with pytest.raises(ValueError, match="chunk_index cannot be negative"):
            SearchChunk(
                tenant=tenant,
                document_id="d1",
                chunk_id="c1",
                chunk_index=-1,
                text="hello",
            )

    def test_text_too_long(self) -> None:
        tenant = _tenant()
        with pytest.raises(ValueError, match="chunk text cannot exceed"):
            SearchChunk(
                tenant=tenant,
                document_id="d1",
                chunk_id="c1",
                chunk_index=0,
                text="x" * 16_385,
            )

    def test_page_number_less_than_one(self) -> None:
        tenant = _tenant()
        with pytest.raises(ValueError, match="page_number must be positive"):
            SearchChunk(
                tenant=tenant,
                document_id="d1",
                chunk_id="c1",
                chunk_index=0,
                text="hello",
                page_number=0,
            )

    def test_start_offset_negative(self) -> None:
        tenant = _tenant()
        with pytest.raises(ValueError, match="start_offset cannot be negative"):
            SearchChunk(
                tenant=tenant,
                document_id="d1",
                chunk_id="c1",
                chunk_index=0,
                text="hello",
                start_offset=-1,
            )

    def test_end_offset_negative(self) -> None:
        tenant = _tenant()
        with pytest.raises(ValueError, match="end_offset cannot be negative"):
            SearchChunk(
                tenant=tenant,
                document_id="d1",
                chunk_id="c1",
                chunk_index=0,
                text="hello",
                end_offset=-1,
            )

    def test_end_offset_less_than_start(self) -> None:
        tenant = _tenant()
        with pytest.raises(
            ValueError, match="end_offset cannot be less than start_offset"
        ):
            SearchChunk(
                tenant=tenant,
                document_id="d1",
                chunk_id="c1",
                chunk_index=0,
                text="hello world",
                start_offset=5,
                end_offset=3,
            )


class TestSearchFiltersValidation:
    def test_min_size_bytes_negative(self) -> None:
        with pytest.raises(ValueError, match="min_size_bytes cannot be negative"):
            SearchFilters(min_size_bytes=-1)

    def test_max_size_bytes_negative(self) -> None:
        with pytest.raises(ValueError, match="max_size_bytes cannot be negative"):
            SearchFilters(max_size_bytes=-1)

    def test_max_less_than_min(self) -> None:
        with pytest.raises(
            ValueError, match="max_size_bytes cannot be less than min_size_bytes"
        ):
            SearchFilters(min_size_bytes=100, max_size_bytes=50)

    def test_created_to_earlier_than_created_from(self) -> None:
        with pytest.raises(
            ValueError, match="created_to cannot be earlier than created_from"
        ):
            SearchFilters(
                created_from=datetime(2026, 6, 1, tzinfo=UTC),
                created_to=datetime(2026, 1, 1, tzinfo=UTC),
            )

    def test_contains_text_too_long(self) -> None:
        with pytest.raises(ValueError, match="contains_text cannot exceed"):
            SearchFilters(contains_text="x" * 1_025)


class TestSearchQueryValidation:
    def test_text_too_long(self) -> None:
        with pytest.raises(ValueError, match="query text cannot exceed"):
            SearchQuery(text="x" * 1_025)

    def test_limit_zero(self) -> None:
        with pytest.raises(ValueError, match="limit must be between"):
            SearchQuery(limit=0)

    def test_limit_exceeds_max(self) -> None:
        with pytest.raises(ValueError, match="limit must be between"):
            SearchQuery(limit=101)


class TestTransactionRollback:
    def test_rollback_on_error(self, tmp_path: Path) -> None:
        from nimbus_runtime.search import FileSearchIndexStore

        store = FileSearchIndexStore(tmp_path)
        store.index_document(
            document=_document(
                tenant=_tenant(),
                document_id="doc-rollback",
            ),
            chunks=(),
        )
        msg = "test rollback"
        with pytest.raises(RuntimeError, match=msg), store._transaction():
            raise RuntimeError(msg)


class TestSearchActorScopeValidation:
    def test_empty_visible_channel_ids_ok(self) -> None:
        actor = _actor(_tenant())
        scope = SearchActorScope(actor=actor, visible_channel_ids=frozenset())
        assert scope.workspace_wide is False


def test_file_search_replaces_chunks_atomically(tmp_path: Path) -> None:
    """Re-indexing a document should replace stale chunks."""
    tenant = _tenant()
    store = FileSearchIndexStore(tmp_path)
    document = _document(tenant=tenant, document_id="doc-reindex")
    scope = SearchActorScope(
        actor=_actor(tenant),
        visible_channel_ids=frozenset({"C123CHAN"}),
    )
    store.index_document(
        document=document,
        chunks=(
            _chunk(
                tenant=tenant,
                document_id=document.document_id,
                chunk_id="chunk-old",
                text="Old Acme wording.",
            ),
        ),
    )
    store.index_document(
        document=document,
        chunks=(
            _chunk(
                tenant=tenant,
                document_id=document.document_id,
                chunk_id="chunk-new",
                text="New Globex wording.",
            ),
        ),
    )

    assert store.search(scope=scope, query=SearchQuery(text="Acme")) == ()
    assert [
        result.document.document_id
        for result in store.search(scope=scope, query=SearchQuery(text="Globex"))
    ] == ["doc-reindex"]
