"""Widen the audit action CHECK for V0.2 versioning events.

Adds DOCUMENT_VERSION_CREATED and DOCUMENT_VERSION_RESTORED (ADR-0007).
This is the whole migration: migration 0002 already shaped
document_versions, blobs, and the current-version pointer for
multi-version documents.

Downgrade re-adds the narrow V0.1 CHECK. On a database that already
contains V0.2 audit events this fails loudly — deliberately: audit is
append-only and we do not delete history to make a downgrade pass.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-12

"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_audit_events_action_valid"
_V01_ACTIONS = "('FOLDER_CREATED','DOCUMENT_CREATED','ENTRY_RENAMED','ENTRY_MOVED')"
_V02_ACTIONS = (
    "('FOLDER_CREATED','DOCUMENT_CREATED','ENTRY_RENAMED','ENTRY_MOVED',"
    "'DOCUMENT_VERSION_CREATED','DOCUMENT_VERSION_RESTORED')"
)


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "audit_events", type_="check")
    op.create_check_constraint(_CONSTRAINT, "audit_events", f"action IN {_V02_ACTIONS}")


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "audit_events", type_="check")
    op.create_check_constraint(_CONSTRAINT, "audit_events", f"action IN {_V01_ACTIONS}")
