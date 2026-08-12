# ADR-0007: Append-only version history — restore creates a new version

**Status:** Accepted — 2026-08-12

## Context

V0.2 turns the single-version kernel into an immutable version chain. The
defining question is what "restore version 1" means when the chain is
`V1, V2, V3 (current)`: rewind the current pointer to V1 (pointer
rollback), or append a V4 that references V1's content?

## Decision

**Version history is append-only, and restore creates a new version.**

1. `document_versions` rows are immutable after commit — including
   `change_note`. No UPDATE statement in the codebase targets the table;
   a recorded mistake is corrected by appending, never by editing. The
   only mutable version-related state is `documents.current_version_id`.
2. **Current is always the highest version number** (`current = max`).
   Both new-version and restore append; nothing ever rewinds the pointer.
3. **Restore appends**: restoring V1 over `V1..V3` creates V4 referencing
   V1's blob (content-addressing makes this free — no bytes move, no
   ContentStore call), with audit action `DOCUMENT_VERSION_RESTORED`
   recording the source version. History reads as what happened:
   `V1 original, V2, V3, V4 restored-from-V1`.
4. **Optimistic concurrency** uses the version *number* as the token:
   `expected_version=N` is checked against the current version number
   under the document row lock; mismatch raises `VersionConflict` and
   writes nothing. `None` means no precondition. This maps directly to
   HTTP ETags (`ETag: "3"` / `If-Match` → 412) at the API milestone.
   The token is sound precisely because of the `current = max` invariant.
5. **Same-content versions are allowed**: a version is a business/history
   event; a blob is content identity. Re-submitting identical bytes is a
   real event and costs one row (the blob dedups). Restore depends on
   this consistency.
6. **No version deletion in V0.2.** Deletion would break `current = max`
   or force renumbering (both poisonous to the concurrency token) and
   drags in blob reference-counting. It belongs to the deletion-lifecycle
   milestone (V0.9), designed together with soft delete, purge, and GC.

## Consequences

- Pointer rollback was rejected because it rewrites what "current" meant
  historically, makes `expected_version` ambiguous after a
  rollback–re-advance cycle, and desynchronizes `current` from `MAX` —
  complicating allocation. Its only benefit is fewer rows.
- Version numbers are monotonic with no gaps or reuse; "what was current
  at time T" is reconstructable from the chain alone.
- Version-number allocation and the `expected_version` check share one
  serialization point: `SELECT … FOR UPDATE` on the `documents` row, with
  `UNIQUE (document_id, version_number)` as the loud backstop.
- Matches restore behavior users know from Laserfiche, SharePoint, and
  S3 object versioning.
