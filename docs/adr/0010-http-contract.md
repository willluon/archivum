# ADR-0010: The HTTP contract — aggregate revision ETags, preconditions, problem details, development actor

**Status:** Accepted — 2026-08-12

## Context

V0.4 introduces FastAPI as the permanent external contract. Content-version
numbers can no longer serve as concurrency tokens: V0.3 metadata mutations
change a document without creating versions, so a version-number ETag lets
stale clients appear current.

## Decisions

1. **Aggregate revision counter on `entries`** (`revision bigint >= 1`).
   The ETag of `GET /documents/{id}` and `GET /folders/{id}` is
   `"<revision>"` and means **representation state**, not content state.
   Incremented by: rename, move, version create, restore, schema
   assignment, metadata set/verify/delete. Not incremented by: reads,
   content verification, audit queries, schema lifecycle changes (schemas
   are a separate resource). Placing it on `entries` (not `documents`)
   protects folders too and keeps one revision per tree resource.
2. **Conditional-bump-first is the serialization point.** Every mutating
   service transaction begins with one atomic statement:
   `UPDATE entries SET revision = revision + 1 WHERE id = :id AND state =
   'active' AND (:expected IS NULL OR revision = :expected) RETURNING
   revision`. Zero rows ⇒ `RevisionConflict` (→ 412) or `EntryNotFound`.
   Check and increment share one statement (no TOCTOU); the entries row
   lock serializes all aggregate mutations; lock ordering is uniformly
   entries → documents (no deadlocks). A failed mutation rolls the bump
   back. `expected_version` (content) survives unchanged as the
   content-specific service contract; the API speaks revision only.
3. **If-Match required on every mutation of an existing document or
   folder**; missing ⇒ **428 Precondition Required**; stale ⇒ **412
   Precondition Failed** with zero mutation and zero audit event.
   `If-Match: *` = existence only. Creation endpoints need none.
4. **Metadata concurrency is whole-document**: a stale write to a
   *different* field still fails 412 — the writer's decision may have
   depended on state that changed. Field-level concurrency is a recorded
   future refinement (the value UUID is the hook). Because
   `expected_revision` lives in service signatures, non-HTTP callers get
   the same protection — this resolves the V0.3 "metadata last-write-wins"
   weakness below the API, not just at it.
5. **Typed resources** (`/folders`, `/documents`) under **`/api/v1`**;
   `entries` stays internal. Wrong-type IDs are 404 at typed endpoints.
   PATCH performs exactly one change (title OR parent) per request —
   rename and move are separate transactions; accepting both would be
   non-atomic. Metadata writes are per-field PUT (1:1 with audit events).
6. **Errors are RFC 9457 problem details** (`application/problem+json`)
   with a stable `code` string per domain error. 409 = state conflicts;
   412 = failed preconditions; 422 = semantically invalid content; no
   driver exceptions or stack traces ever leak.
7. **SHA-256 is public integrity metadata**; content endpoints use it as
   their (strong) ETag. Storage keys and filesystem paths never appear in
   any response.
8. **`X-Archivum-Actor` header — explicitly NOT authentication.** A
   development-only actor-attribution mechanism, required on mutations so
   audit is never unattributed, replaced wholesale at the authorization
   milestone by swapping one FastAPI dependency. No principal endpoints.
9. **Pagination is offset/limit** (`items/total/limit/offset`) on children
   and audit; cursor pagination and range requests are documented
   deferrals. Version and schema lists are unpaginated (bounded
   cardinality).
10. **The CLI becomes a pure API client** (httpx) in V0.4; `archivum
    serve` runs the ASGI app. If a CLI command cannot be expressed over
    HTTP, the API is wrong — that is the point.
11. **Hardening (V0.3 weakness #1):** a constraint trigger on
    `metadata_fields` blocks INSERT/DELETE and structural UPDATE
    (key/field_type/schema_id/required) unless the owning schema is
    `draft` — the composite-FK soundness argument of ADR-0008 no longer
    rests on service discipline alone. Display columns stay freely
    editable.
12. **Deliberately deferred/preserved:** schema-level optimistic
    concurrency (drafts serialize on row locks; last writer wins on
    labels); manual-values-born-verified (four-eyes is a workflow-milestone
    policy; `set_by` ≠ `verified_by` is already expressible); metadata
    value history (additive later; audit remains value-free); schema name
    reuse after retirement (audit carries UUID + name-at-event-time).

## Consequences

- Two counters coexist by design: `DocumentVersion.version_number` is
  content history; `entries.revision` is representation state. They must
  never be conflated.
- Any future mutation of the document aggregate must bump the revision or
  ETags silently lie — noted as a review point for every future milestone.
- Offset pagination and single-change PATCH are acknowledged contract
  costs at portfolio scale.
