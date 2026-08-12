# ADR-0006: Repository kernel schema decisions

**Status:** Accepted — 2026-08-12

## Context

The V0.1 kernel design checkpoint produced a set of schema-level decisions
that don't warrant individual ADRs but must not be re-litigated by accident.
Recorded here as ratified.

## Decisions

1. **Sibling-title uniqueness** is case-insensitive, applies across both
   entry types, and is scoped to live entries: unique index on
   `(parent_id, lower(title)) WHERE state = 'active'`.
2. **"Parent must be a folder" is enforced by PostgreSQL** via a generated
   constant column + composite foreign key: `entries` carries
   `UNIQUE (id, entry_type)`; children carry
   `parent_type GENERATED ALWAYS AS ('folder' when parented)` and
   `FK (parent_id, parent_type) → entries (id, entry_type)`. The mirror
   trick on `documents` (`entry_type GENERATED ALWAYS AS ('document')`)
   guarantees the satellite only attaches to document entries. Documents
   can never have children; folders can never have versions.
3. **`documents.current_version_id` is nullable in schema** (circular
   dependency with `document_versions`); "never null after commit" is a
   service + test invariant. A composite FK
   `(current_version_id, entry_id) → document_versions (id, document_id)`
   makes cross-document version pointers impossible.
4. **`audit_events.target_id` deliberately has no foreign key** so audit
   history survives its target, including future purge. The absence of the
   constraint is the design.
5. **Audit PK is `bigint` identity** — internal, insert-ordered, compact.
6. **Hashes are `bytea`** (32 raw bytes, CHECKed), rendered hex in tooling.
7. **`entries.state` ships in V0.1** (only `'active'` written) so the title
   constraint is soft-delete-ready before V0.9 exists.
8. **UUIDv7, app-generated** via stdlib `uuid.uuid7()`; the project requires
   Python ≥ 3.14. Postgres 17 has no native v7 generator.
9. **`document_versions.original_filename`** records immutable arrival
   provenance, distinct from the mutable `entries.title`.
10. **Cycle prevention lives in the service layer** (ancestor walk under
    `SELECT … FOR UPDATE` locks) with invariant tests; a database trigger
    backstop is deferred until real write concurrency exists (V0.5+).

## Consequences

- The database refuses structurally-invalid states (documents with
  children, borrowed current versions, duplicate roots, duplicate sibling
  titles) even if service code regresses.
- Two invariants remain code-enforced and therefore test-pinned: current
  version non-null after commit, and acyclicity under concurrent moves.
- Well-known rows are seeded by migration: the `system` principal and the
  root folder, at fixed sentinel UUIDs.
