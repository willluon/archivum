"""Principals: who acts. Minimal in V0.1 so audit actors are real (ADR-0004).

Well-known rows are seeded by migration 0002 at fixed sentinel UUIDs.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.engine import Engine

from archivum.domain import new_id

SYSTEM_PRINCIPAL_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
ROOT_ENTRY_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")


def ensure_user_principal(engine: Engine, display_name: str) -> uuid.UUID:
    """Get-or-create a user principal by display name (V0.1 CLI convenience;
    real identity arrives at V0.8)."""
    from archivum.db.tables import principals

    with engine.begin() as conn:
        existing = conn.execute(
            select(principals.c.id)
            .where(
                principals.c.display_name == display_name,
                principals.c.principal_type == "user",
            )
            .order_by(principals.c.created_at)
            .limit(1)
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        pid = new_id()
        conn.execute(
            principals.insert().values(id=pid, principal_type="user", display_name=display_name)
        )
        return pid
