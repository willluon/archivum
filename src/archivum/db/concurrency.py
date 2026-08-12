"""Aggregate revision bump: check + increment in one atomic statement (ADR-0010).

Every mutating service transaction calls this FIRST. The conditional UPDATE
is simultaneously the If-Match precondition check, the revision increment,
and (via its row lock) the serialization point for all mutations of the
entry's aggregate. A failed transaction rolls the bump back.
"""

import uuid

from sqlalchemy import func, select, update
from sqlalchemy.engine import Connection

from archivum.db.tables import entries
from archivum.domain import EntryNotFound, RevisionConflict


def bump_revision(
    conn: Connection, entry_id: uuid.UUID, expected_revision: int | None = None
) -> int:
    stmt = (
        update(entries)
        .where(entries.c.id == entry_id, entries.c.state == "active")
        .values(revision=entries.c.revision + 1, updated_at=func.now())
        .returning(entries.c.revision)
    )
    if expected_revision is not None:
        stmt = stmt.where(entries.c.revision == expected_revision)
    new_revision = conn.execute(stmt).scalar_one_or_none()
    if new_revision is None:
        actual = conn.execute(
            select(entries.c.revision).where(
                entries.c.id == entry_id, entries.c.state == "active"
            )
        ).scalar_one_or_none()
        if actual is None:
            raise EntryNotFound(str(entry_id))
        raise RevisionConflict(expected_revision, actual)
    return new_revision
