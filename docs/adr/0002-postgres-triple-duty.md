# ADR-0002: PostgreSQL serves three duties — relational core, full-text search, job queue

**Status:** Accepted — 2026-08-12

## Context

An ECM needs (a) transactional relational state with real constraints,
(b) full-text search over document text, and (c) an asynchronous job queue
for OCR, thumbnails, indexing, and other processors. The enterprise reflex is
three systems: an RDBMS, Elasticsearch/OpenSearch, and Redis/RabbitMQ +
Celery. This project is built by one developer and must optimize for honest,
inspectable engineering over infrastructure theater.

## Decision

PostgreSQL (17, via Docker Compose) serves all three duties initially:

- **Relational core** — foreign keys, CHECK constraints, transactions,
  Alembic migrations, UUID keys.
- **Full-text search** — `tsvector` columns with GIN indexes, treated
  strictly as derived state with a rebuild command.
- **Job queue** — a jobs table claimed with
  `SELECT … FOR UPDATE SKIP LOCKED`, giving safe concurrent workers,
  retries, and dead-lettering without a broker.

SQLAlchemy 2.0 (Core-leaning, explicit table definitions) + Alembic manage
schema; no auto-generated schema is committed unreviewed.

## Consequences

- One system to run, back up, and reason about; transactional consistency
  between business state and queue state comes free (a job can be enqueued
  in the same transaction as the change that requires it).
- Search capability is bounded by Postgres FTS (no fuzzy ranking tuning,
  vector search, or cross-cluster scale). Acceptable at portfolio scale.
- Upgrade paths are explicit and per-duty: OpenSearch behind the search
  module's interface; a real broker behind the processing module's interface.
  Each would be its own ADR with a reason grounded in observed need.
- Queue throughput is bounded by row locking — orders of magnitude beyond
  this project's needs.
