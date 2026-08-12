# archivum

A portfolio-scale **Enterprise Content Management (ECM) platform** — a document
repository with durable document identity, content-addressed storage, dynamic
metadata schemas, versioning, audit, search, and asynchronous processing.

**Status: V0.3 — metadata schemas.** Documents have permanent UUID identity
surviving rename, move, and versioning; bytes live in a content-addressed
`ContentStore`; version history is append-only with restore-as-new-version
(ADR-0007) and optimistic concurrency. Document types ("Building Permit",
"Invoice") are now **data, not code**: typed-EAV metadata with per-value
provenance, confidence that survives human verification, and a schema
lifecycle that freezes structure once published (ADR-0008/0009) — with
cross-schema writes and type mismatches rejected by PostgreSQL itself.
Every mutation still writes an append-only audit event in the same
transaction (metadata audit carries field identity, never values).
CLI: `mkdir` / `ingest` / `ls` / `info` / `rename` / `mv` / `versions` /
`version-add` / `restore` / `schema …` / `metadata …` / `audit` /
`verify`; `scripts/demo.sh` walks the whole lifecycle in CI. Next: the API
milestone (see [`docs/roadmap.md`](docs/roadmap.md)).

## What this is

archivum grows out of [a real capture tool](docs/current-system.md) I built and
ran in production during a municipal Building Department internship: a scanner
ingestion assistant that classifies scanned permits, extracts structured fields
(OCR + vision-model fallback), validates them against county reference data, and
routes everything through human verification before filing into a commercial ECM
(Laserfiche). Working next to Laserfiche taught me the larger problem space —
this project explores it from first principles at a scale one developer can
build honestly.

It is **not** intended to replace Laserfiche, be deployed by any municipality,
or hold production records. It is a serious independent system-design project.

## Core principles

- **A document is a durable logical entity, not a file path.** Identity is a
  permanent UUID; rename, move, re-metadata, and re-version never change it.
- **Logical state, binary content, and search index are separate concerns**
  with an explicit canonical-vs-derived split: if the search index is lost,
  no document is lost, and a rebuild command restores it.
- **Human-in-the-loop is architecture, not apology.** Extracted metadata is a
  suggestion until a person (or an explicit confidence policy) verifies it;
  verification is the canonicalization boundary, and every metadata value
  carries provenance.
- **Audit is not debug logging.** Business events (who did what to which
  object) are append-only records written in the same transaction as the
  change they describe.

## Stack

Python · PostgreSQL (relational core, full-text search, and job queue — one
system, three duties, each with a documented upgrade path) · SQLAlchemy 2.0 +
Alembic · FastAPI (from the API milestone) · content-addressed filesystem blob
store behind a `ContentStore` interface. Modular monolith; no microservices,
no Elasticsearch, no Kafka — see the ADRs for why not (yet).

## Development

```bash
docker compose up -d          # PostgreSQL 17 on localhost:5432
pip install -e ".[dev]"
alembic upgrade head
pytest
ruff check .
```

`DATABASE_URL` overrides the default connection string
(`postgresql+psycopg://archivum:archivum@localhost:5432/archivum`).

## Documentation

- [`docs/roadmap.md`](docs/roadmap.md) — milestones V0 → V1.0 and beyond
- [`docs/architecture/overview.md`](docs/architecture/overview.md) — target
  architecture, domain model, canonical-vs-derived state map
- [`docs/adr/`](docs/adr/) — architecture decision records
- [`docs/current-system.md`](docs/current-system.md) — the predecessor capture
  tool and the design lessons carried forward
- [`docs/data-policy.md`](docs/data-policy.md) — why no real municipal data
  ever enters this repository
