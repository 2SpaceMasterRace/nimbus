# Nimbus Search And Knowledge Layer

Feature 11 starts as a rebuildable runtime projection, not a new source of
truth. Storage objects, task state, policy decisions, and artifacts remain
authoritative elsewhere; search indexes metadata and extracted chunks so clients
can retrieve cited evidence quickly.

## Contract

The runtime exposes these primitives from `nimbus_runtime`:

| Primitive | Purpose |
| --- | --- |
| `SearchDocument` | Tenant-scoped file metadata, indexing state, ACL hints, and facets. |
| `SearchChunk` | Bounded untrusted text extracted from one document. |
| `SearchActorScope` | Verified actor plus explicit channel or workspace visibility from a trusted adapter or policy layer. |
| `SearchQuery` | Text, filters, and bounded result count. |
| `FileSearchIndexStore` | SQLite fallback for local development and deterministic tests. |
| `PostgresSearchIndexStore` | Production store over the Postgres search tables and full-text index. |

Search results include chunk citations such as
`slack://T123/C123/F123:page:2:chunk:4`. A caller that wants an LLM-written
answer must ground the answer only in returned chunks the actor is allowed to
see.

## Invariants

1. Candidate documents are filtered by tenant and ACL before lexical scoring.
2. Extracted text is untrusted user content, not instructions.
3. Extraction failures are represented explicitly with
   `SearchDocumentStatus.EXTRACTION_FAILED` and no fabricated text chunks.
4. Search is a projection. It can be rebuilt from files, manifests, artifacts,
   and connector metadata.
5. Result sets, chunks, and queries are bounded so a malformed corpus cannot
   force unbounded memory growth.

## Current Scope

The first slice supports metadata indexing, bounded text chunks, structured
filters, SQLite local search, Postgres schema migration, and ACL-aware lexical
results. OCR, embeddings, reranking, answer synthesis, and Slack command wiring
are intentionally separate follow-up slices so they can inherit this contract
instead of weakening it.

## Failure Behavior

| Failure | Behavior |
| --- | --- |
| Malformed or unsupported file | Index metadata, record extraction failure, and continue. |
| Prompt injection inside a file | Return text as untrusted cited evidence only. |
| Actor has no channel/workspace/direct grant | Return no result for that document. |
| Re-indexing a file | Replace old chunks atomically for that document. |
| Stale projection | Return `indexed_at` so clients can display freshness. |

The Postgres migration creates `search_documents`, `search_chunks`, and a
GIN-backed `search_vector`. The SQLite fallback keeps the same logical model
without introducing extra infrastructure for local mode.
