# ADR-0004: Audit events are append-only and written in the same transaction as the change

**Status:** Accepted — 2026-08-12

## Context

Application logs answer "why did something crash"; audit answers "who did
what to which business object, when." Conflating them (as the predecessor
tool does, harmlessly at single-user scale) makes the audit trail exactly as
trustworthy as the least careful log line. An audit record that can be lost
when a request fails halfway, or edited after the fact, is not an audit
record.

## Decision

1. Every state-changing repository operation writes an `AuditEvent` row
   (actor, action, target, timestamp, details) **inside the transaction that
   performs the change**. The change and its audit record commit or roll
   back together; neither exists without the other.
2. The audit table is **append-only by code path**: no update or delete
   functions exist in the codebase. Later, a dedicated database role without
   UPDATE/DELETE grants on the table makes this demonstrable rather than
   promised.
3. Actors are real from day one: a minimal `principals` table exists in the
   V0.1 kernel (seeded system + developer principals) solely so `actor_id`
   is never null or fake. Full identity/authorization comes much later;
   retrofitting actors into historical audit rows is not possible, which is
   why this small piece ships first.
4. Audit history survives the object it describes — deleting (even purging)
   a document never deletes its audit trail.

## Consequences

- Audit coverage becomes a testable invariant: every mutating service
  operation has a corresponding event; tests assert it.
- In-transaction writes cost one INSERT per operation — negligible, and the
  same pattern later extends to the transactional outbox for domain events.
- Debug/application logging remains entirely separate (stdlib `logging`),
  free to be verbose, rotated, and lossy.
