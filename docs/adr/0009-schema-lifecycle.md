# ADR-0009: Schema lifecycle — draft → active → retired, structurally immutable after publication

**Status:** Accepted — 2026-08-12

## Context

When a schema changes after 100 documents use it, existing metadata must
not become silently invalid and historical meaning must stay explainable.
Mutable-in-place schemas fail both (a new required field retroactively
un-completes every document; a type change makes stored values wrong).
Full schema versioning (SchemaIdentity → SchemaVersion chains with field
mapping and document migration) is a milestone of its own.

## Decision

Schemas have an explicit one-way lifecycle:

```text
draft ──publish──▶ active ──retire──▶ retired
```

- **draft** — structurally editable (add/remove fields, types, required);
  NOT assignable to documents.
- **active** — assignable; structure frozen. Display edits (schema name,
  field labels, descriptions, positions) remain allowed — display is not
  identity.
- **retired** — not assignable to new documents; existing assignments and
  values keep working forever. Retiring frees the schema name (uniqueness
  is case-insensitive among non-retired schemas).

Structural change after publication = **create a new schema**. No version
chains in V0.3 — and no trap: published schemas are already frozen
versions; a future identity/grouping table can link them without touching
existing rows.

Related ratified rules:

1. **Explicit publish, not freeze-on-first-assignment** — immutability as
   an implicit side effect of assignment is racy and uninspectable; a
   state is one word to explain and is itself audited.
2. **Zero or one schema per document**, as a nullable
   `documents.metadata_schema_id` column. Replacing it is prohibited once
   values exist (service error, and the ADR-0008 composite FK enforces it
   in PostgreSQL); allowed while value-free. Multi-schema is deferred
   without a placeholder.
3. **Field identity = immutable `key`** (`^[a-z][a-z0-9_]*$`, unique per
   schema); `label` is mutable display — the document-title lesson applied
   to fields. `field_type` is immutable always.
4. **`required` means completeness, not existence.** Documents exist with
   incomplete metadata (ingestion reality); `required` feeds a derived
   completeness check (`missing_required` / `complete`), computed on read,
   enforced by nothing in V0.3. Future processing/workflow gates consume it.
5. **No `extraction_hint` column yet** — no consumer exists until the
   processing milestone; adding a nullable column then is a one-line
   migration. (Reverses the V0 overview sketch, deliberately.)

## Consequences

- The metadata_values composite FKs are sound precisely because active
  schemas are structurally frozen — the lifecycle and the constraint
  system lock together.
- Admins wanting to evolve a published schema create a successor schema
  and (later milestone) migrate documents explicitly; nothing happens
  implicitly.
- Migration 0004's downgrade refuses to run if metadata exists — dropping
  tables that hold user metadata is not reversibility (V0.2 precedent).
