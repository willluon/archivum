# ADR-0001: Separate repository; modular monolith

**Status:** Accepted — 2026-08-12

## Context

archivum's predecessor is a working scanner-capture tool (Permit Scanning
Assist, see `docs/current-system.md`) still in daily production use. That tool
is a deliberately single-file desktop app whose architecture is correct for
what it is; it identifies documents by file path, has no job model, and couples
orchestration to its UI. An ECM needs different foundations: durable identity,
migrations, a service boundary, asynchronous processing.

Two structural questions: does the ECM grow inside the existing codebase or
start fresh, and is it one deployable or many?

## Decision

1. **archivum is a new repository with a new codebase.** The capture tool is
   untouched; it later integrates as an API client (roadmap V0.7).
2. **archivum is a modular monolith**: one Python package, one deployable,
   with strict internal module boundaries (`domain/` imports no outer layer;
   `api/` reaches state only through services). No microservices.

## Consequences

- The API boundary between capture client and repository is *enforced by
  repository separation*, not merely requested by convention.
- The public archivum repo can guarantee it contains no data from the
  production deployment (see `docs/data-policy.md`); the predecessor stays
  private.
- The predecessor remains stable while archivum evolves; no risk of a
  refactor-in-place destabilizing a production tool.
- Reuse from the predecessor is deliberate porting (concepts, one extraction
  engine) rather than accidental inheritance of its constraints.
- If module boundaries ever need to become service boundaries, the module
  layout is the extraction map — but that is not a goal.
