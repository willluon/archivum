"""Repository kernel: principals, blobs, entries, documents, document_versions, audit_events.

Design: ADR-0005 (single entries table), ADR-0006 (schema decisions),
ADR-0003 (content addressing), ADR-0004 (audit).

Seeds two well-known rows at fixed sentinel UUIDs: the system principal and
the root folder.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-12

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TIMESTAMP, UUID

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

SYSTEM_PRINCIPAL_ID = "00000000-0000-0000-0000-000000000001"
ROOT_ENTRY_ID = "00000000-0000-0000-0000-000000000002"


def upgrade() -> None:
    op.create_table(
        "principals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("principal_type", sa.Text, nullable=False),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column(
            "created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "principal_type IN ('user','service','system')", name="ck_principals_type_valid"
        ),
        sa.CheckConstraint("display_name <> ''", name="ck_principals_display_name_nonempty"),
    )

    op.create_table(
        "blobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("sha256", BYTEA, nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("storage_key", sa.Text, nullable=False),
        sa.Column(
            "created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("octet_length(sha256) = 32", name="ck_blobs_sha256_len"),
        sa.CheckConstraint("size_bytes >= 0", name="ck_blobs_size_nonneg"),
        sa.UniqueConstraint("sha256", name="uq_blobs_sha256"),
        sa.UniqueConstraint("storage_key", name="uq_blobs_storage_key"),
    )

    op.create_table(
        "entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("entry_type", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("parent_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "parent_type",
            sa.Text,
            sa.Computed(
                "CASE WHEN parent_id IS NULL THEN NULL ELSE 'folder' END", persisted=True
            ),
            nullable=True,
        ),
        sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'active'")),
        sa.Column(
            "created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "entry_type IN ('folder','document')", name="ck_entries_entry_type_valid"
        ),
        sa.CheckConstraint(
            "title <> '' AND length(title) <= 255", name="ck_entries_title_valid"
        ),
        sa.CheckConstraint("state IN ('active','deleted')", name="ck_entries_state_valid"),
        sa.UniqueConstraint("id", "entry_type", name="uq_entries_id"),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["principals.id"],
            name="fk_entries_created_by_principals",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id", "parent_type"],
            ["entries.id", "entries.entry_type"],
            name="fk_entries_parent_id_entries",
            ondelete="RESTRICT",
        ),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_entries_sibling_title ON entries (parent_id, lower(title)) "
        "WHERE state = 'active'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_entries_single_root ON entries ((true)) WHERE parent_id IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_entries_parent_active ON entries (parent_id) WHERE state = 'active'"
    )

    op.create_table(
        "documents",
        sa.Column("entry_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("entry_type", sa.Text, sa.Computed("'document'", persisted=True), nullable=False),
        sa.Column("current_version_id", UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["entry_id", "entry_type"],
            ["entries.id", "entries.entry_type"],
            name="fk_documents_entry_id_entries",
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "document_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("blob_id", UUID(as_uuid=True), nullable=False),
        sa.Column("mime_type", sa.Text, nullable=False),
        sa.Column("original_filename", sa.Text, nullable=True),
        sa.Column("change_note", sa.Text, nullable=True),
        sa.Column(
            "created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "version_number >= 1", name="ck_document_versions_version_number_positive"
        ),
        sa.UniqueConstraint(
            "document_id", "version_number", name="uq_document_versions_document_id"
        ),
        sa.UniqueConstraint("id", "document_id", name="uq_document_versions_id"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.entry_id"],
            name="fk_document_versions_document_id_documents",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["blob_id"], ["blobs.id"], name="fk_document_versions_blob_id_blobs",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["principals.id"],
            name="fk_document_versions_created_by_principals",
            ondelete="RESTRICT",
        ),
    )

    # Circular dependency documents <-> document_versions: add the
    # current-version FK now that both tables exist. Composite target
    # guarantees the current version belongs to this document (ADR-0006 §3).
    op.create_foreign_key(
        "fk_documents_current_version",
        "documents",
        "document_versions",
        ["current_version_id", "entry_id"],
        ["id", "document_id"],
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger, sa.Identity(always=True), primary_key=True),
        sa.Column(
            "occurred_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("actor_id", UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("target_type", sa.Text, nullable=False, server_default=sa.text("'entry'")),
        # No FK on target_id: audit history must survive its target (ADR-0006 §4)
        sa.Column("target_id", UUID(as_uuid=True), nullable=False),
        sa.Column("details", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.CheckConstraint(
            "action IN ('FOLDER_CREATED','DOCUMENT_CREATED','ENTRY_RENAMED','ENTRY_MOVED')",
            name="ck_audit_events_action_valid",
        ),
        sa.CheckConstraint("target_type IN ('entry')", name="ck_audit_events_target_type_valid"),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["principals.id"],
            name="fk_audit_events_actor_id_principals",
            ondelete="RESTRICT",
        ),
    )
    op.execute("CREATE INDEX ix_audit_events_target ON audit_events (target_id, id)")

    # Well-known seed rows (fixed sentinel UUIDs, ADR-0006)
    op.execute(
        "INSERT INTO principals (id, principal_type, display_name) "
        f"VALUES ('{SYSTEM_PRINCIPAL_ID}', 'system', 'system')"
    )
    op.execute(
        "INSERT INTO entries (id, entry_type, title, parent_id, created_by) "
        f"VALUES ('{ROOT_ENTRY_ID}', 'folder', '/', NULL, '{SYSTEM_PRINCIPAL_ID}')"
    )


def downgrade() -> None:
    op.drop_table("audit_events")
    op.drop_constraint("fk_documents_current_version", "documents", type_="foreignkey")
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.drop_table("entries")
    op.drop_table("blobs")
    op.drop_table("principals")
