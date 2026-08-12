# Roadmap

Each milestone stays runnable, introduces few concepts, ships tests, and has
a concrete completion criterion. Versions are sequential but not sacred —
ordering changes get recorded here with a reason.

## V0 — Foundation *(done 2026-08-12)*

Repo scaffold, Docker Compose Postgres, Alembic baseline, CI (lint, migrate
up/down, tests), ADRs 0001–0005, this documentation set.
**Done when:** CI is green — including `alembic upgrade head` against a real
Postgres — on an empty-schema baseline.

## V0.1 — Repository kernel *(done 2026-08-12)*

Tables: `principals`, `entries`, `documents`, `document_versions` (single
version), `blobs`, `audit_events`. `ContentStore` interface + filesystem
implementation (content-addressed, ADR-0003). Services: create folder,
ingest document, get, rename, move, list. Thin CLI as the demo surface.
**Done when:** invariant tests pass — rename/move preserve ID; identical
bytes → one blob, two documents; blob-write failure leaves no DB row; DB
failure after blob write leaves only a harmless orphan blob; every mutation
has an in-transaction audit event; title collisions and cycles are rejected —
and a scripted CLI walkthrough (ingest → rename → move → fetch by unchanged
ID → audit trail → hash verify) runs clean.

## V0.2 — Versioning

v2…vN with an immutable version chain and current-version pointer.
Optimistic-concurrency hook (`expected_version` parameter) designed into the
service signatures now, enforced at the API milestone via ETags.
**Done when:** creating vN+1 provably never mutates vN; version history is
listable; hash verification works per version.

## V0.3 — Metadata schemas

`MetadataSchema` / `MetadataFieldDefinition` / `MetadataValue` with type
validation and per-value provenance + confidence (the predecessor's
source-rank lesson, generalized). Field definitions carry an inert
`extraction_hint` for the future intelligence layer.
**Done when:** "Building Permit" and "Invoice" document types are both
defined as data — zero code changes between them — and values type-check.
Open questions to resolve here: value storage (typed EAV vs JSONB), schema
versioning semantics.

## V0.4 — API

FastAPI over the services; the permanent contract. ETags from version
numbers; no storage paths or internal IDs beyond the public UUIDs leak.
**Done when:** every kernel operation is exercised through HTTP in tests;
the CLI is reimplemented as an API client.

## V0.5 — Asynchronous processing

`processing_jobs` table, SKIP LOCKED worker, retries with backoff,
dead-letter state, idempotency keys; transactional outbox for domain events.
First processors: text extraction, thumbnail generation.
**Done when:** a killed worker mid-job loses nothing; a poisoned job dead-
letters after max attempts; processors are idempotent under forced retry.

## V0.6 — Search

Postgres FTS (`tsvector`/GIN) over extracted text + structured metadata
queries through one endpoint. `reindex` command rebuilds from canonical
state.
**Done when:** dropping the index and reindexing yields identical results —
the derived-state claim, demonstrated.

## V0.7 — Capture client integration

The predecessor scanner gains an optional "submit to archivum" path behind a
config flag (its existing flow untouched); metadata arrives with provenance
carried over. Demonstrated with synthetic scans only.
**Done when:** a scanned batch lands in the repository with document IDs,
versions, metadata, and audit trail, end to end.

## V0.8 — Authorization

ACL entries on entries (single ID space per ADR-0005), folder inheritance,
group principals. Permission tests including the unauthorized-read behavior
decision (403 vs 404).
**Done when:** an unauthorized principal can neither read content nor
confirm a document's existence, and permission checks are tested at the API
boundary.

## V0.9 — Deletion lifecycle

Soft delete → recycle bin → explicit purge as distinct, audited actions.
Audit history survives purge. Blob GC design lands here.
**Done when:** delete is reversible, purge is explicit and irreversible, and
the audit trail of a purged document remains queryable.

## V1.0 — Cohesive demo

Generated synthetic corpus (permits, invoices, contracts for a fictional
municipality), seeded walkthrough, docs current, possibly a minimal
read-only web UI if it earns its place.

## Later — designed subsystems, each with its own ADR

Workflow engine (definitions vs instances, persistent execution state) ·
records management (retention, cutoff, holds, disposition) · generalized AI
extraction (extraction hints come alive; the predecessor's permit extractor
as the first pluggable implementation) · forms/structured intake · semantic
search.
