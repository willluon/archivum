# ADR-0003: Content-addressed blob storage with blob-first write ordering

**Status:** Accepted — 2026-08-12

## Context

Document bytes must live outside the relational database, behind an
abstraction (`ContentStore`) so the physical backend can change (local
filesystem now; MinIO/S3 later). Two identities must not be conflated:
**document identity** (a UUID, permanent, logical) and **content identity**
(a hash of the bytes). Two documents may share identical bytes.

The classic ECM consistency hazard: the database says a document exists but
its bytes were never stored, or bytes are stored with no database record.
Distributed transactions across a DB and a filesystem don't exist.

## Decision

1. Blobs are **content-addressed**: stored under their SHA-256
   (`blobs/ab/cd/abcd…`), written via temp-file + atomic rename. Identical
   bytes are stored once; the `blobs` table row carries hash, size, and
   storage key.
2. **Write ordering is blob-first.** Ingestion writes bytes to the
   ContentStore, then runs one database transaction creating the blob row,
   entry, document, version, and audit event. The DB never references bytes
   that do not exist.
3. The tolerated failure mode is an **orphaned blob** (bytes stored, DB
   transaction failed): harmless, invisible to users, detectable by a future
   `fsck`/garbage-collection pass. The intolerable failure mode — a document
   row whose content is missing — is structurally prevented.
4. `verify()` re-hashes stored bytes against the recorded SHA-256; integrity
   is checkable at any time.

## Consequences

- Deduplication is free; the Claude-vision result cache pattern from the
  predecessor generalizes (derived results keyed by content hash).
- Blob deletion requires reference counting or GC — deliberately deferred
  (documents are soft-deleted long before blobs are ever purged; see the
  deletion-lifecycle milestone).
- Content addressing makes blobs immutable by construction, which is exactly
  what version immutability requires.
