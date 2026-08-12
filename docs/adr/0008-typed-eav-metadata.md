# ADR-0008: Typed-EAV metadata storage with per-value provenance

**Status:** Accepted — 2026-08-12

## Context

V0.3 makes document types definable as data ("Building Permit", "Invoice")
without code changes. The storage question: one JSONB blob per document, or
one typed row per (document, field)?

## Decision

**Typed EAV.** `metadata_values` holds one row per (document, field) with
per-type columns (`value_text`, `value_integer`, `value_decimal`,
`value_boolean`, `value_date`, `value_datetime`) — exactly one populated,
matching the field's declared type — plus first-class provenance columns.

JSONB was rejected because Archivum's central metadata requirement is
**per-field provenance, confidence, and verification** (the predecessor
capture tool's hardest-won lesson). In JSONB that nests into
`{"field": {"value":…, "origin":…}}` — EAV rebuilt inside JSON with no
database help: no type safety (dates and decimals become strings by
convention), no referential integrity (keys drift from field definitions),
no row identity for audit targets, history, or future field-level ACLs.

Key points:

1. **A metadata value is a first-class object** with UUID identity plus
   `UNIQUE (document_id, field_id)` — one value per field per document.
2. **Three composite FKs make correctness declarative** (the ADR-0006
   generated-column technique, extended): `(field_id, schema_id) →
   metadata_fields (id, schema_id)` (field belongs to the value's schema);
   `(document_id, schema_id) → documents (entry_id, metadata_schema_id)`
   (the schema is the document's assigned schema — cross-schema writes are
   structurally impossible, and as a side effect PostgreSQL itself blocks
   replacing a document's schema while values exist); `(field_id,
   field_type) → metadata_fields (id, field_type)` (the denormalized type
   driving the value-column CHECK provably matches the field definition).
3. **Provenance**: `origin IN (manual, extracted, imported, system)` —
   how the current value came to be. Validation/confirmation is NOT an
   origin; it is verification state (`verified_at`/`verified_by`, both-or-
   neither CHECK). `confidence` (0–1, CHECKed, nullable) records how the
   machine felt and **survives human verification** — the record keeps
   both "machine said 0.91" and "human accepted it". Manual values are
   born verified (a human typing is confirmation); machine values are born
   unverified; any overwrite resets verification. Machine output is a
   suggestion until confirmed.
4. **Metadata changes do not create document versions.** DocumentVersion
   is content history only; metadata converges through extraction →
   verification cycles and would bury content history in noise. Metadata
   change history = audit trail for now; a value-history table is deferred
   work for which the value UUID is the hook.
5. **Audit events never contain metadata values** — not old, not new, not
   hashed (low-entropy values like dates and booleans make hashes
   trivially reversible, so hashing is exposure in disguise). Audit
   answers who/which-field/when/how-originated; "what was it before" is
   the deferred history table's job.

## Consequences

- Reads join; row count grows per field — trivial at any realistic scale.
- Type enforcement is layered: service `parse_value` (strict; rejects
  NaN/Infinity decimals, naive datetimes), then typed columns + CHECKs as
  the backstop.
- The search milestone gets typed, indexable columns; a derived JSONB
  projection remains possible later as rebuildable state if reads warrant.
