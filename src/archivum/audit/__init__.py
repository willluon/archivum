"""Audit: append-only business events, written in-transaction (ADR-0004).

record_event takes the caller's Connection so the event commits or rolls
back atomically with the change it describes. There is deliberately no
update or delete function in this module — append-only is a code-path
guarantee pinned by tests.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.engine import Connection

from archivum.db.tables import AUDIT_ACTIONS, AUDIT_TARGET_TYPES, audit_events


def record_event(
    conn: Connection,
    *,
    actor_id: uuid.UUID,
    action: str,
    target_id: uuid.UUID,
    details: dict,
    target_type: str = "entry",
) -> None:
    if action not in AUDIT_ACTIONS:
        raise ValueError(f"unknown audit action: {action}")
    if target_type not in AUDIT_TARGET_TYPES:
        raise ValueError(f"unknown audit target type: {target_type}")
    conn.execute(
        audit_events.insert().values(
            actor_id=actor_id, action=action, target_type=target_type, target_id=target_id,
            details=details,
        )
    )


def trail(conn: Connection, target_id: uuid.UUID) -> list[dict]:
    rows = conn.execute(
        select(
            audit_events.c.id,
            audit_events.c.occurred_at,
            audit_events.c.actor_id,
            audit_events.c.action,
            audit_events.c.details,
        )
        .where(audit_events.c.target_id == target_id)
        .order_by(audit_events.c.id)
    ).all()
    return [dict(r._mapping) for r in rows]
