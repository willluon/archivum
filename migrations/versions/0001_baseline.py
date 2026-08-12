"""Baseline: empty schema root.

Exists so the migration chain has a fixed origin and so `alembic upgrade head`
exercises a real database connection from V0 onward. The V0.1 repository
kernel introduces the first tables in a revision on top of this one.

Revision ID: 0001
Revises:
Create Date: 2026-08-12

"""

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
