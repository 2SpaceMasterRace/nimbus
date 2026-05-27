"""ACL-aware search projection primitives for Nimbus knowledge retrieval."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, cast

from psycopg.types.json import Jsonb

from nimbus_runtime.domain import TenantIdentity, VerifiedActor
from nimbus_runtime.postgres import connect as pg_connect
from nimbus_runtime.postgres import transaction as pg_transaction

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from pathlib import Path

    from psycopg import Connection as PostgresConnection

_DB_FILENAME = "nimbus_runtime.sqlite3"
_SCHEMA_VERSION = 1
_BUSY_TIMEOUT_MS = 5_000
_MAX_QUERY_CHARS = 1_024
_MAX_SEARCH_LIMIT = 100
_DEFAULT_SEARCH_LIMIT = 10
_MAX_CANDIDATE_DOCUMENTS = 1_000
_MAX_CHUNK_CHARS = 16_384
_SNIPPET_CONTEXT_CHARS = 80
_MAX_SNIPPET_CHARS = 240
_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*")
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class SearchDocumentStatus(StrEnum):
    """Indexing state for one searchable document projection."""

    SEARCHABLE = "searchable"
    EXTRACTION_PENDING = "extraction_pending"
    EXTRACTION_FAILED = "extraction_failed"
    QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True)
class SearchDocument:
    """Metadata projection for one tenant-scoped source document."""

    tenant: TenantIdentity
    document_id: str
    source_uri: str
    object_key: str
    title: str
    content_type: str
    size_bytes: int
    status: SearchDocumentStatus
    workspace_id: str | None = None
    channel_id: str | None = None
    uploader_id: str | None = None
    bucket: str | None = None
    saved: bool | None = None
    duplicate_group: str | None = None
    sha256_hex: str | None = None
    source_created_at: datetime | None = None
    indexed_at: datetime | None = None
    extraction_error: str | None = None
    visible_to_actor_ids: tuple[str, ...] = ()
    visible_to_channel_ids: tuple[str, ...] = ()
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate the document projection before it reaches a store."""
        _require_non_empty(self.document_id, field_name="document_id")
        _require_non_empty(self.source_uri, field_name="source_uri")
        _require_non_empty(self.object_key, field_name="object_key")
        _require_non_empty(self.title, field_name="title")
        _require_non_empty(self.content_type, field_name="content_type")
        if self.size_bytes < 0:
            msg = "size_bytes cannot be negative"
            raise ValueError(msg)
        _require_no_empty_values(
            self.visible_to_actor_ids,
            field_name="visible_to_actor_ids",
        )
        _require_no_empty_values(
            self.visible_to_channel_ids,
            field_name="visible_to_channel_ids",
        )

    @property
    def effective_channel_acl(self) -> frozenset[str]:
        """Return explicit channel grants, falling back to the source channel."""
        if self.visible_to_channel_ids:
            return frozenset(self.visible_to_channel_ids)
        if self.channel_id is None:
            return frozenset()
        return frozenset({self.channel_id})


@dataclass(frozen=True, slots=True)
class SearchChunk:
    """One text chunk extracted from a searchable document."""

    tenant: TenantIdentity
    document_id: str
    chunk_id: str
    chunk_index: int
    text: str
    page_number: int | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate the chunk before storing untrusted extracted text."""
        _require_non_empty(self.document_id, field_name="document_id")
        _require_non_empty(self.chunk_id, field_name="chunk_id")
        if self.chunk_index < 0:
            msg = "chunk_index cannot be negative"
            raise ValueError(msg)
        if len(self.text) > _MAX_CHUNK_CHARS:
            msg = f"chunk text cannot exceed {_MAX_CHUNK_CHARS} characters"
            raise ValueError(msg)
        if self.page_number is not None and self.page_number < 1:
            msg = "page_number must be positive when provided"
            raise ValueError(msg)
        if self.start_offset is not None and self.start_offset < 0:
            msg = "start_offset cannot be negative"
            raise ValueError(msg)
        if self.end_offset is not None and self.end_offset < 0:
            msg = "end_offset cannot be negative"
            raise ValueError(msg)
        if (
            self.start_offset is not None
            and self.end_offset is not None
            and self.end_offset < self.start_offset
        ):
            msg = "end_offset cannot be less than start_offset"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class SearchActorScope:
    """Actor-bound visibility scope supplied by a trusted adapter or policy."""

    actor: VerifiedActor
    visible_channel_ids: frozenset[str] = frozenset()
    workspace_wide: bool = False

    def __post_init__(self) -> None:
        """Reject malformed permission scopes before search execution."""
        _require_no_empty_values(
            tuple(self.visible_channel_ids),
            field_name="visible_channel_ids",
        )


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """Structured filters applied before lexical search and ranking."""

    workspace_id: str | None = None
    channel_id: str | None = None
    uploader_id: str | None = None
    content_type: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    min_size_bytes: int | None = None
    max_size_bytes: int | None = None
    saved: bool | None = None
    duplicate_group: str | None = None
    bucket: str | None = None
    contains_text: str | None = None
    sha256_hex: str | None = None
    status: SearchDocumentStatus | None = None

    def __post_init__(self) -> None:
        """Validate filter bounds before query planning."""
        if self.min_size_bytes is not None and self.min_size_bytes < 0:
            msg = "min_size_bytes cannot be negative"
            raise ValueError(msg)
        if self.max_size_bytes is not None and self.max_size_bytes < 0:
            msg = "max_size_bytes cannot be negative"
            raise ValueError(msg)
        if (
            self.min_size_bytes is not None
            and self.max_size_bytes is not None
            and self.max_size_bytes < self.min_size_bytes
        ):
            msg = "max_size_bytes cannot be less than min_size_bytes"
            raise ValueError(msg)
        if (
            self.created_from is not None
            and self.created_to is not None
            and self.created_to < self.created_from
        ):
            msg = "created_to cannot be earlier than created_from"
            raise ValueError(msg)
        if (
            self.contains_text is not None
            and len(self.contains_text) > _MAX_QUERY_CHARS
        ):
            msg = f"contains_text cannot exceed {_MAX_QUERY_CHARS} characters"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """Search request over the rebuildable knowledge projection."""

    text: str = ""
    filters: SearchFilters = field(default_factory=SearchFilters)
    limit: int = _DEFAULT_SEARCH_LIMIT

    def __post_init__(self) -> None:
        """Validate query shape and bound result size."""
        if len(self.text) > _MAX_QUERY_CHARS:
            msg = f"query text cannot exceed {_MAX_QUERY_CHARS} characters"
            raise ValueError(msg)
        if not 1 <= self.limit <= _MAX_SEARCH_LIMIT:
            msg = f"limit must be between 1 and {_MAX_SEARCH_LIMIT}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class SearchChunkHit:
    """Cited chunk selected from an ACL-visible document."""

    chunk_id: str
    chunk_index: int
    snippet: str
    score: float
    page_number: int | None = None
    start_offset: int | None = None
    end_offset: int | None = None


@dataclass(frozen=True, slots=True)
class SearchResult:
    """One ACL-filtered search result with cited chunk evidence."""

    document: SearchDocument
    score: float
    chunk_hits: tuple[SearchChunkHit, ...]
    citations: tuple[str, ...]
    indexed_at: datetime | None
    content_warnings: tuple[str, ...] = ("untrusted_extracted_text",)


class SearchIndexStore(Protocol):
    """Durable rebuildable search projection for Nimbus files and chunks."""

    def index_document(
        self,
        *,
        document: SearchDocument,
        chunks: Sequence[SearchChunk],
    ) -> None:
        """Upsert one document and replace its extracted chunks atomically."""

    def search(
        self,
        *,
        scope: SearchActorScope,
        query: SearchQuery,
    ) -> Sequence[SearchResult]:
        """Return ACL-filtered, cited search results."""


class FileSearchIndexStore:
    """SQLite-backed search index for local Nimbus deployments and tests."""

    def __init__(self, root: Path) -> None:
        """Create a search projection under ``root``."""
        self._root = root
        self._db_path = root / _DB_FILENAME
        self._lock = _path_lock(self._db_path)

    def index_document(
        self,
        *,
        document: SearchDocument,
        chunks: Sequence[SearchChunk],
    ) -> None:
        """Upsert one document and replace its chunks in one transaction."""
        _validate_document_chunks(document=document, chunks=chunks)
        indexed_at = document.indexed_at or datetime.now(UTC)
        document = _with_indexed_at(document, indexed_at)
        with self._transaction() as con:
            self._upsert_document(con, document)
            con.execute(
                """
                DELETE FROM search_chunks
                WHERE tenant_id = ? AND document_id = ?
                """,
                (document.tenant.tenant_id, document.document_id),
            )
            for chunk in chunks:
                self._insert_chunk(con, chunk)

    def search(
        self,
        *,
        scope: SearchActorScope,
        query: SearchQuery,
    ) -> Sequence[SearchResult]:
        """Return ACL-filtered, lexical search results with citations."""
        with self._lock:
            con = self._connect()
            try:
                candidates = self._candidate_documents(
                    con,
                    tenant=scope.actor.tenant,
                    filters=query.filters,
                )
                visible = tuple(
                    document
                    for document in candidates
                    if _actor_can_see_document(scope=scope, document=document)
                )
                chunks_by_document = self._chunks_for_documents(
                    con,
                    tenant=scope.actor.tenant,
                    document_ids=tuple(document.document_id for document in visible),
                )
            finally:
                con.close()
        return _rank_results(
            documents=visible,
            chunks_by_document=chunks_by_document,
            query=query,
        )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            con = self._connect()
            try:
                con.execute("BEGIN IMMEDIATE")
                yield con
            except Exception:
                con.execute("ROLLBACK")
                raise
            else:
                con.execute("COMMIT")
            finally:
                con.close()

    def _connect(self) -> sqlite3.Connection:
        self._root.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self._db_path, timeout=5.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
        con.execute("PRAGMA journal_mode = WAL")
        con.execute("PRAGMA synchronous = FULL")
        self._ensure_schema(con)
        return con

    @staticmethod
    def _ensure_schema(con: sqlite3.Connection) -> None:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS search_documents (
                tenant_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                tenant_json TEXT NOT NULL,
                source_uri TEXT NOT NULL,
                object_key TEXT NOT NULL,
                title TEXT NOT NULL,
                content_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                status TEXT NOT NULL,
                workspace_id TEXT,
                channel_id TEXT,
                uploader_id TEXT,
                bucket TEXT,
                saved INTEGER,
                duplicate_group TEXT,
                sha256_hex TEXT,
                source_created_at TEXT,
                indexed_at TEXT NOT NULL,
                extraction_error TEXT,
                visible_actor_ids_json TEXT NOT NULL,
                visible_channel_ids_json TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                PRIMARY KEY (tenant_id, document_id)
            );
            CREATE INDEX IF NOT EXISTS search_documents_by_channel
                ON search_documents (tenant_id, channel_id, indexed_at);
            CREATE INDEX IF NOT EXISTS search_documents_by_bucket
                ON search_documents (tenant_id, bucket, indexed_at);
            CREATE INDEX IF NOT EXISTS search_documents_by_status
                ON search_documents (tenant_id, status, indexed_at);
            CREATE TABLE IF NOT EXISTS search_chunks (
                tenant_id TEXT NOT NULL,
                document_id TEXT NOT NULL,
                chunk_id TEXT NOT NULL,
                chunk_index INTEGER NOT NULL,
                text TEXT NOT NULL,
                page_number INTEGER,
                start_offset INTEGER,
                end_offset INTEGER,
                metadata_json TEXT NOT NULL,
                schema_version INTEGER NOT NULL,
                PRIMARY KEY (tenant_id, document_id, chunk_id),
                FOREIGN KEY (tenant_id, document_id)
                    REFERENCES search_documents (tenant_id, document_id)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS search_chunks_by_document
                ON search_chunks (tenant_id, document_id, chunk_index);
            """
        )

    @staticmethod
    def _upsert_document(con: sqlite3.Connection, document: SearchDocument) -> None:
        con.execute(
            """
            INSERT INTO search_documents (
                tenant_id, document_id, tenant_json, source_uri, object_key,
                title, content_type, size_bytes, status, workspace_id, channel_id,
                uploader_id, bucket, saved, duplicate_group, sha256_hex,
                source_created_at, indexed_at, extraction_error,
                visible_actor_ids_json, visible_channel_ids_json, metadata_json,
                schema_version
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT (tenant_id, document_id) DO UPDATE SET
                tenant_json = excluded.tenant_json,
                source_uri = excluded.source_uri,
                object_key = excluded.object_key,
                title = excluded.title,
                content_type = excluded.content_type,
                size_bytes = excluded.size_bytes,
                status = excluded.status,
                workspace_id = excluded.workspace_id,
                channel_id = excluded.channel_id,
                uploader_id = excluded.uploader_id,
                bucket = excluded.bucket,
                saved = excluded.saved,
                duplicate_group = excluded.duplicate_group,
                sha256_hex = excluded.sha256_hex,
                source_created_at = excluded.source_created_at,
                indexed_at = excluded.indexed_at,
                extraction_error = excluded.extraction_error,
                visible_actor_ids_json = excluded.visible_actor_ids_json,
                visible_channel_ids_json = excluded.visible_channel_ids_json,
                metadata_json = excluded.metadata_json,
                schema_version = excluded.schema_version
            """,
            _document_sql_values(document),
        )

    @staticmethod
    def _insert_chunk(con: sqlite3.Connection, chunk: SearchChunk) -> None:
        con.execute(
            """
            INSERT INTO search_chunks (
                tenant_id, document_id, chunk_id, chunk_index, text, page_number,
                start_offset, end_offset, metadata_json, schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _chunk_sql_values(chunk),
        )

    def _candidate_documents(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        filters: SearchFilters,
    ) -> tuple[SearchDocument, ...]:
        clauses: list[str] = ["tenant_id = ?"]
        params: list[object] = [tenant.tenant_id]
        _append_sql_filters(clauses=clauses, params=params, filters=filters, style="?")
        sql = (
            "SELECT * FROM search_documents "  # noqa: S608 - Fixed clauses.
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY indexed_at DESC LIMIT ?"
        )
        params.append(_MAX_CANDIDATE_DOCUMENTS)
        rows = con.execute(sql, tuple(params)).fetchall()
        return tuple(
            document
            for row in rows
            if (document := _safe_document_from_sqlite(row)) is not None
        )

    def _chunks_for_documents(
        self,
        con: sqlite3.Connection,
        *,
        tenant: TenantIdentity,
        document_ids: Sequence[str],
    ) -> dict[str, tuple[SearchChunk, ...]]:
        if not document_ids:
            return {}
        rows = con.execute(
            """
            SELECT *
            FROM search_chunks
            WHERE tenant_id = ?
              AND document_id IN (SELECT value FROM json_each(?))
            ORDER BY document_id ASC, chunk_index ASC
            """,
            (tenant.tenant_id, _json_dumps(list(document_ids))),
        ).fetchall()
        chunks_by_document: defaultdict[str, list[SearchChunk]] = defaultdict(list)
        for row in rows:
            chunk = _safe_chunk_from_sqlite(row, tenant=tenant)
            if chunk is not None:
                chunks_by_document[chunk.document_id].append(chunk)
        return {
            document_id: tuple(chunks)
            for document_id, chunks in chunks_by_document.items()
        }


class PostgresSearchIndexStore:
    """Postgres-backed search index using tenant and ACL filters before FTS."""

    def index_document(
        self,
        *,
        document: SearchDocument,
        chunks: Sequence[SearchChunk],
    ) -> None:
        """Upsert one document and replace its chunks in one transaction."""
        _validate_document_chunks(document=document, chunks=chunks)
        indexed_at = document.indexed_at or datetime.now(UTC)
        document = _with_indexed_at(document, indexed_at)
        with pg_transaction() as con:
            self._upsert_document(con, document)
            con.execute(
                """
                DELETE FROM search_chunks
                WHERE tenant_id = %s AND document_id = %s
                """,
                (document.tenant.tenant_id, document.document_id),
            )
            for chunk in chunks:
                self._insert_chunk(con, chunk)

    def search(
        self,
        *,
        scope: SearchActorScope,
        query: SearchQuery,
    ) -> Sequence[SearchResult]:
        """Return ACL-filtered Postgres full-text search results."""
        with pg_connect() as con:
            documents = self._candidate_documents(con=con, scope=scope, query=query)
            chunks_by_document = self._chunks_for_documents(
                con=con,
                tenant=scope.actor.tenant,
                document_ids=tuple(document.document_id for document in documents),
            )
        return _rank_results(
            documents=documents,
            chunks_by_document=chunks_by_document,
            query=query,
        )

    @staticmethod
    def _upsert_document(
        con: PostgresConnection[dict[str, object]],
        document: SearchDocument,
    ) -> None:
        con.execute(
            """
            INSERT INTO search_documents (
                tenant_id, document_id, tenant_json, source_uri, object_key,
                title, content_type, size_bytes, status, workspace_id, channel_id,
                uploader_id, bucket, saved, duplicate_group, sha256_hex,
                source_created_at, indexed_at, extraction_error,
                visible_actor_ids, visible_channel_ids, metadata_json, schema_version
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (tenant_id, document_id) DO UPDATE SET
                tenant_json = excluded.tenant_json,
                source_uri = excluded.source_uri,
                object_key = excluded.object_key,
                title = excluded.title,
                content_type = excluded.content_type,
                size_bytes = excluded.size_bytes,
                status = excluded.status,
                workspace_id = excluded.workspace_id,
                channel_id = excluded.channel_id,
                uploader_id = excluded.uploader_id,
                bucket = excluded.bucket,
                saved = excluded.saved,
                duplicate_group = excluded.duplicate_group,
                sha256_hex = excluded.sha256_hex,
                source_created_at = excluded.source_created_at,
                indexed_at = excluded.indexed_at,
                extraction_error = excluded.extraction_error,
                visible_actor_ids = excluded.visible_actor_ids,
                visible_channel_ids = excluded.visible_channel_ids,
                metadata_json = excluded.metadata_json,
                schema_version = excluded.schema_version
            """,
            (
                document.tenant.tenant_id,
                document.document_id,
                Jsonb(_tenant_to_json(document.tenant)),
                document.source_uri,
                document.object_key,
                document.title,
                document.content_type,
                document.size_bytes,
                document.status.value,
                document.workspace_id,
                document.channel_id,
                document.uploader_id,
                document.bucket,
                document.saved,
                document.duplicate_group,
                document.sha256_hex,
                document.source_created_at,
                document.indexed_at,
                document.extraction_error,
                list(document.visible_to_actor_ids),
                list(document.visible_to_channel_ids),
                Jsonb(dict(document.metadata)),
                _SCHEMA_VERSION,
            ),
        )

    @staticmethod
    def _insert_chunk(
        con: PostgresConnection[dict[str, object]],
        chunk: SearchChunk,
    ) -> None:
        con.execute(
            """
            INSERT INTO search_chunks (
                tenant_id, document_id, chunk_id, chunk_index, text, page_number,
                start_offset, end_offset, metadata_json, schema_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                chunk.tenant.tenant_id,
                chunk.document_id,
                chunk.chunk_id,
                chunk.chunk_index,
                chunk.text,
                chunk.page_number,
                chunk.start_offset,
                chunk.end_offset,
                Jsonb(dict(chunk.metadata)),
                _SCHEMA_VERSION,
            ),
        )

    def _candidate_documents(
        self,
        *,
        con: PostgresConnection[dict[str, object]],
        scope: SearchActorScope,
        query: SearchQuery,
    ) -> tuple[SearchDocument, ...]:
        clauses = ["tenant_id = %s", _postgres_acl_clause()]
        params: list[object] = [
            scope.actor.tenant.tenant_id,
            scope.workspace_wide,
            scope.actor.user_id,
            list(scope.visible_channel_ids),
            list(scope.visible_channel_ids),
        ]
        _append_sql_filters(
            clauses=clauses,
            params=params,
            filters=query.filters,
            style="%s",
        )
        text = _search_text(query)
        if text:
            clauses.append(
                """
                EXISTS (
                    SELECT 1
                    FROM search_chunks c
                    WHERE c.tenant_id = search_documents.tenant_id
                      AND c.document_id = search_documents.document_id
                      AND c.search_vector @@ websearch_to_tsquery('english', %s)
                )
                """
            )
            params.append(text)
        sql = (
            "SELECT * FROM search_documents "  # noqa: S608 - Fixed clauses.
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY indexed_at DESC LIMIT %s"
        )
        params.append(_MAX_CANDIDATE_DOCUMENTS)
        rows = con.execute(sql, tuple(params)).fetchall()
        return tuple(
            document
            for row in rows
            if (document := _safe_document_from_mapping(row)) is not None
        )

    def _chunks_for_documents(
        self,
        *,
        con: PostgresConnection[dict[str, object]],
        tenant: TenantIdentity,
        document_ids: Sequence[str],
    ) -> dict[str, tuple[SearchChunk, ...]]:
        if not document_ids:
            return {}
        rows = con.execute(
            """
            SELECT *
            FROM search_chunks
            WHERE tenant_id = %s AND document_id = ANY(%s)
            ORDER BY document_id ASC, chunk_index ASC
            """,
            (tenant.tenant_id, list(document_ids)),
        ).fetchall()
        chunks_by_document: defaultdict[str, list[SearchChunk]] = defaultdict(list)
        for row in rows:
            chunk = _safe_chunk_from_mapping(row, tenant=tenant)
            if chunk is not None:
                chunks_by_document[chunk.document_id].append(chunk)
        return {
            document_id: tuple(chunks)
            for document_id, chunks in chunks_by_document.items()
        }


def _path_lock(path: Path) -> threading.RLock:
    resolved = path.resolve()
    with _LOCKS_GUARD:
        lock = _LOCKS.get(resolved)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[resolved] = lock
        return lock


def _require_non_empty(value: str, *, field_name: str) -> None:
    if not value:
        msg = f"{field_name} is required"
        raise ValueError(msg)


def _require_no_empty_values(values: Sequence[str], *, field_name: str) -> None:
    if any(not value for value in values):
        msg = f"{field_name} cannot contain empty values"
        raise ValueError(msg)


def _validate_document_chunks(
    *,
    document: SearchDocument,
    chunks: Sequence[SearchChunk],
) -> None:
    for chunk in chunks:
        if chunk.tenant != document.tenant:
            msg = "chunk tenant must match document tenant"
            raise ValueError(msg)
        if chunk.document_id != document.document_id:
            msg = "chunk document_id must match document document_id"
            raise ValueError(msg)
    chunk_ids = [chunk.chunk_id for chunk in chunks]
    if len(set(chunk_ids)) != len(chunk_ids):
        msg = "chunk ids must be unique per indexed document"
        raise ValueError(msg)


def _with_indexed_at(
    document: SearchDocument,
    indexed_at: datetime,
) -> SearchDocument:
    return SearchDocument(
        tenant=document.tenant,
        document_id=document.document_id,
        source_uri=document.source_uri,
        object_key=document.object_key,
        title=document.title,
        content_type=document.content_type,
        size_bytes=document.size_bytes,
        status=document.status,
        workspace_id=document.workspace_id,
        channel_id=document.channel_id,
        uploader_id=document.uploader_id,
        bucket=document.bucket,
        saved=document.saved,
        duplicate_group=document.duplicate_group,
        sha256_hex=document.sha256_hex,
        source_created_at=document.source_created_at,
        indexed_at=indexed_at,
        extraction_error=document.extraction_error,
        visible_to_actor_ids=document.visible_to_actor_ids,
        visible_to_channel_ids=document.visible_to_channel_ids,
        metadata=document.metadata,
    )


def _tenant_to_json(tenant: TenantIdentity) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "platform": tenant.platform,
        "workspace_id": tenant.workspace_id,
    }


def _tenant_from_json(data: Mapping[str, object]) -> TenantIdentity:
    return TenantIdentity(
        platform=_required_str(data, "platform"),
        workspace_id=_required_str(data, "workspace_id"),
    )


def _json_dumps(payload: Mapping[str, object] | Sequence[str]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _json_loads_mapping(raw: str, *, field_name: str) -> dict[str, object]:
    value = json.loads(raw)
    if not isinstance(value, dict):
        msg = f"{field_name} must be a JSON object"
        raise TypeError(msg)
    return cast("dict[str, object]", value)


def _json_loads_str_tuple(raw: str, *, field_name: str) -> tuple[str, ...]:
    value = json.loads(raw)
    if not isinstance(value, list):
        msg = f"{field_name} must be a JSON array"
        raise TypeError(msg)
    parsed: list[str] = []
    for item in value:
        if not isinstance(item, str):
            msg = f"{field_name} entries must be strings"
            raise TypeError(msg)
        parsed.append(item)
    return tuple(parsed)


def _datetime_to_json(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


def _datetime_from_value(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    msg = f"expected datetime value, got {value!r}"
    raise TypeError(msg)


def _required_str(data: Mapping[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        msg = f"expected string field {key!r}"
        raise TypeError(msg)
    return value


def _optional_str_from_value(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    msg = f"expected optional string value, got {value!r}"
    raise TypeError(msg)


def _required_int_from_value(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"expected integer value, got {value!r}"
        raise TypeError(msg)
    return value


def _optional_int_from_value(value: object) -> int | None:
    if value is None:
        return None
    return _required_int_from_value(value)


def _optional_bool_from_value(value: object) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        msg = f"expected optional boolean value, got {value!r}"
        raise TypeError(msg)
    return value


def _str_tuple_from_value(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return _json_loads_str_tuple(value, field_name="acl")
    if isinstance(value, list):
        parsed: list[str] = []
        for item in value:
            if not isinstance(item, str):
                msg = "ACL entries must be strings"
                raise TypeError(msg)
            parsed.append(item)
        return tuple(parsed)
    if isinstance(value, tuple):
        parsed_tuple: list[str] = []
        for item in value:
            if not isinstance(item, str):
                msg = "ACL entries must be strings"
                raise TypeError(msg)
            parsed_tuple.append(item)
        return tuple(parsed_tuple)
    msg = f"expected ACL sequence, got {value!r}"
    raise TypeError(msg)


def _metadata_from_value(value: object) -> Mapping[str, object]:
    if value is None:
        return {}
    if isinstance(value, str):
        return _json_loads_mapping(value, field_name="metadata")
    if isinstance(value, dict):
        return cast("Mapping[str, object]", value)
    msg = f"expected metadata mapping, got {value!r}"
    raise TypeError(msg)


def _document_sql_values(document: SearchDocument) -> tuple[object, ...]:
    return (
        document.tenant.tenant_id,
        document.document_id,
        _json_dumps(_tenant_to_json(document.tenant)),
        document.source_uri,
        document.object_key,
        document.title,
        document.content_type,
        document.size_bytes,
        document.status.value,
        document.workspace_id,
        document.channel_id,
        document.uploader_id,
        document.bucket,
        None if document.saved is None else int(document.saved),
        document.duplicate_group,
        document.sha256_hex,
        _datetime_to_json(document.source_created_at),
        _datetime_to_json(document.indexed_at) or _datetime_to_json(datetime.now(UTC)),
        document.extraction_error,
        _json_dumps(list(document.visible_to_actor_ids)),
        _json_dumps(list(document.visible_to_channel_ids)),
        _json_dumps(dict(document.metadata)),
        _SCHEMA_VERSION,
    )


def _chunk_sql_values(chunk: SearchChunk) -> tuple[object, ...]:
    return (
        chunk.tenant.tenant_id,
        chunk.document_id,
        chunk.chunk_id,
        chunk.chunk_index,
        chunk.text,
        chunk.page_number,
        chunk.start_offset,
        chunk.end_offset,
        _json_dumps(dict(chunk.metadata)),
        _SCHEMA_VERSION,
    )


def _safe_document_from_sqlite(row: sqlite3.Row) -> SearchDocument | None:
    try:
        return _document_from_mapping(dict(row))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _safe_document_from_mapping(row: Mapping[str, object]) -> SearchDocument | None:
    try:
        return _document_from_mapping(row)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _document_from_mapping(row: Mapping[str, object]) -> SearchDocument:
    if _required_int_from_value(row["schema_version"]) != _SCHEMA_VERSION:
        msg = "unsupported search document schema version"
        raise ValueError(msg)
    tenant = _tenant_from_json(_metadata_from_value(row["tenant_json"]))
    return SearchDocument(
        tenant=tenant,
        document_id=_required_row_str(row, "document_id"),
        source_uri=_required_row_str(row, "source_uri"),
        object_key=_required_row_str(row, "object_key"),
        title=_required_row_str(row, "title"),
        content_type=_required_row_str(row, "content_type"),
        size_bytes=_required_int_from_value(row["size_bytes"]),
        status=SearchDocumentStatus(_required_row_str(row, "status")),
        workspace_id=_optional_str_from_value(row.get("workspace_id")),
        channel_id=_optional_str_from_value(row.get("channel_id")),
        uploader_id=_optional_str_from_value(row.get("uploader_id")),
        bucket=_optional_str_from_value(row.get("bucket")),
        saved=_saved_from_value(row.get("saved")),
        duplicate_group=_optional_str_from_value(row.get("duplicate_group")),
        sha256_hex=_optional_str_from_value(row.get("sha256_hex")),
        source_created_at=_datetime_from_value(row.get("source_created_at")),
        indexed_at=_datetime_from_value(row.get("indexed_at")),
        extraction_error=_optional_str_from_value(row.get("extraction_error")),
        visible_to_actor_ids=_str_tuple_from_value(
            row.get("visible_actor_ids", row.get("visible_actor_ids_json")),
        ),
        visible_to_channel_ids=_str_tuple_from_value(
            row.get("visible_channel_ids", row.get("visible_channel_ids_json")),
        ),
        metadata=_metadata_from_value(row.get("metadata_json")),
    )


def _safe_chunk_from_sqlite(
    row: sqlite3.Row,
    *,
    tenant: TenantIdentity,
) -> SearchChunk | None:
    try:
        return _chunk_from_mapping(dict(row), tenant=tenant)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _safe_chunk_from_mapping(
    row: Mapping[str, object],
    *,
    tenant: TenantIdentity,
) -> SearchChunk | None:
    try:
        return _chunk_from_mapping(row, tenant=tenant)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _chunk_from_mapping(
    row: Mapping[str, object],
    *,
    tenant: TenantIdentity,
) -> SearchChunk:
    if _required_int_from_value(row["schema_version"]) != _SCHEMA_VERSION:
        msg = "unsupported search chunk schema version"
        raise ValueError(msg)
    return SearchChunk(
        tenant=tenant,
        document_id=_required_row_str(row, "document_id"),
        chunk_id=_required_row_str(row, "chunk_id"),
        chunk_index=_required_int_from_value(row["chunk_index"]),
        text=_required_row_str(row, "text"),
        page_number=_optional_int_from_value(row.get("page_number")),
        start_offset=_optional_int_from_value(row.get("start_offset")),
        end_offset=_optional_int_from_value(row.get("end_offset")),
        metadata=_metadata_from_value(row.get("metadata_json")),
    )


def _required_row_str(row: Mapping[str, object], key: str) -> str:
    value = row[key]
    if not isinstance(value, str):
        msg = f"expected string column {key!r}"
        raise TypeError(msg)
    return value


def _saved_from_value(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    msg = f"expected optional saved boolean, got {value!r}"
    raise TypeError(msg)


def _append_sql_filters(
    *,
    clauses: list[str],
    params: list[object],
    filters: SearchFilters,
    style: str,
) -> None:
    optional_equal_filters: tuple[tuple[str, object | None], ...] = (
        ("workspace_id", filters.workspace_id),
        ("channel_id", filters.channel_id),
        ("uploader_id", filters.uploader_id),
        ("content_type", filters.content_type),
        ("duplicate_group", filters.duplicate_group),
        ("bucket", filters.bucket),
        ("sha256_hex", filters.sha256_hex),
        ("status", None if filters.status is None else filters.status.value),
    )
    for column, value in optional_equal_filters:
        if value is None:
            continue
        clauses.append(f"{column} = {style}")
        params.append(value)
    if filters.saved is not None:
        clauses.append(f"saved = {style}")
        params.append(filters.saved)
    if filters.created_from is not None:
        clauses.append(f"source_created_at >= {style}")
        params.append(_datetime_to_json(filters.created_from))
    if filters.created_to is not None:
        clauses.append(f"source_created_at <= {style}")
        params.append(_datetime_to_json(filters.created_to))
    if filters.min_size_bytes is not None:
        clauses.append(f"size_bytes >= {style}")
        params.append(filters.min_size_bytes)
    if filters.max_size_bytes is not None:
        clauses.append(f"size_bytes <= {style}")
        params.append(filters.max_size_bytes)


def _postgres_acl_clause() -> str:
    return """
    (
        %s
        OR %s = ANY(visible_actor_ids)
        OR visible_channel_ids && %s::text[]
        OR channel_id = ANY(%s::text[])
    )
    """


def _actor_can_see_document(
    *,
    scope: SearchActorScope,
    document: SearchDocument,
) -> bool:
    if scope.actor.tenant != document.tenant:
        return False
    if scope.workspace_wide:
        return True
    if scope.actor.user_id in document.visible_to_actor_ids:
        return True
    channel_acl = document.effective_channel_acl
    return bool(channel_acl and channel_acl.intersection(scope.visible_channel_ids))


def _rank_results(
    *,
    documents: Sequence[SearchDocument],
    chunks_by_document: Mapping[str, Sequence[SearchChunk]],
    query: SearchQuery,
) -> tuple[SearchResult, ...]:
    text = _search_text(query)
    tokens = _tokens(text)
    results: list[SearchResult] = []
    for document in documents:
        chunks = tuple(chunks_by_document.get(document.document_id, ()))
        if not _matches_contains_text(
            document=document,
            chunks=chunks,
            contains_text=query.filters.contains_text,
        ):
            continue
        if text and document.status is not SearchDocumentStatus.SEARCHABLE:
            continue
        chunk_hits = _chunk_hits(chunks=chunks, text=text, tokens=tokens)
        title_score = _title_score(document.title, tokens)
        if text and not chunk_hits and title_score == 0:
            continue
        score = title_score + sum(hit.score for hit in chunk_hits)
        if not text:
            score = 1.0
        citations = tuple(
            _citation(document=document, hit=hit) for hit in chunk_hits[:3]
        )
        results.append(
            SearchResult(
                document=document,
                score=score,
                chunk_hits=chunk_hits[:3],
                citations=citations,
                indexed_at=document.indexed_at,
            )
        )
    results.sort(
        key=lambda result: (
            -result.score,
            -_sort_timestamp(result.indexed_at),
            result.document.document_id,
        ),
    )
    return tuple(results[: query.limit])


def _search_text(query: SearchQuery) -> str:
    parts = [query.text.strip()]
    if query.filters.contains_text is not None:
        parts.append(query.filters.contains_text.strip())
    return " ".join(part for part in parts if part)


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(match.group(0).lower() for match in _TOKEN_RE.finditer(text))


def _matches_contains_text(
    *,
    document: SearchDocument,
    chunks: Sequence[SearchChunk],
    contains_text: str | None,
) -> bool:
    if contains_text is None or not contains_text.strip():
        return True
    needle = contains_text.lower()
    return needle in document.title.lower() or any(
        needle in chunk.text.lower() for chunk in chunks
    )


def _chunk_hits(
    *,
    chunks: Sequence[SearchChunk],
    text: str,
    tokens: Sequence[str],
) -> tuple[SearchChunkHit, ...]:
    if not text:
        return ()
    hits: list[SearchChunkHit] = []
    for chunk in chunks:
        score = _text_score(chunk.text, tokens)
        if score == 0:
            continue
        hits.append(
            SearchChunkHit(
                chunk_id=chunk.chunk_id,
                chunk_index=chunk.chunk_index,
                snippet=_snippet(chunk.text, text=text, tokens=tokens),
                score=score,
                page_number=chunk.page_number,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
            )
        )
    hits.sort(key=lambda hit: (-hit.score, hit.chunk_index, hit.chunk_id))
    return tuple(hits)


def _title_score(title: str, tokens: Sequence[str]) -> float:
    return 3.0 * _text_score(title, tokens)


def _text_score(value: str, tokens: Sequence[str]) -> float:
    if not tokens:
        return 0.0
    lowered = value.lower()
    return float(sum(lowered.count(token) for token in tokens))


def _snippet(chunk_text: str, *, text: str, tokens: Sequence[str]) -> str:
    lowered = chunk_text.lower()
    needle_index = -1
    for candidate in (text.lower(), *tokens):
        if not candidate:
            continue
        needle_index = lowered.find(candidate)
        if needle_index >= 0:
            break
    if needle_index < 0:
        return chunk_text[:_MAX_SNIPPET_CHARS]
    start = max(0, needle_index - _SNIPPET_CONTEXT_CHARS)
    end = min(len(chunk_text), needle_index + _MAX_SNIPPET_CHARS)
    snippet = chunk_text[start:end]
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(chunk_text):
        snippet = f"{snippet}..."
    return snippet[:_MAX_SNIPPET_CHARS]


def _citation(*, document: SearchDocument, hit: SearchChunkHit) -> str:
    page = "" if hit.page_number is None else f":page:{hit.page_number}"
    return f"{document.source_uri}{page}:chunk:{hit.chunk_index}"


def _sort_timestamp(value: datetime | None) -> float:
    if value is None:
        return 0.0
    return value.timestamp()
