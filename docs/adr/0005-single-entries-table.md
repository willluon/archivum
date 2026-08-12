# ADR-0005: The repository tree is a single `entries` table

**Status:** Accepted — 2026-08-12

## Context

Folders and documents both live in the logical repository hierarchy. They can
be modeled as one table with a type discriminator, or as two precisely-shaped
tables. Every tree-wide behavior — listing, rename, move, soft-delete, audit
targeting, and (later) ACLs with folder inheritance and shortcut nodes —
follows from this choice. Laserfiche models everything as an "entry" with a
single entry-ID space, which is part of why its ACL and audit models stay
uniform.

## Decision

One `entries` table holds every tree node:

```
entries: id (UUID, permanent identity), type ('folder'|'document'),
         title, parent_id → entries.id, state ('active'|'deleted'),
         created_at, created_by → principals.id
```

Document-specific state lives in a 1:1 satellite `documents` table
(`entry_id → entries.id`, current version pointer, document type);
versions and blobs hang off documents.

Tree invariants enforced at this table: unique title within a parent (per
type), no cycles, parent must be a folder, root is fixed.

## Consequences

- Move/rename/list/delete/audit are written once against one table; a later
  `shortcut` type is one enum value, not a third table.
- ACL entries and audit events target a single ID space (`entry_id`).
- Cost: type-dependent integrity (only documents have versions; only folders
  have children) is enforced by CHECK constraints, service-layer rules, and
  tests rather than purely by table shape. Accepted.
- `parent_id` adjacency is the hierarchy representation; recursive CTEs serve
  path queries. Materialized paths/closure tables are an optimization we
  adopt only if measured need appears.
