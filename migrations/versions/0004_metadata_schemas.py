"""Metadata schemas, typed-EAV values, provenance (ADR-0008, ADR-0009).

Tables: metadata_schemas (draft/active/retired lifecycle), metadata_fields
(stable key identity, immutable type), metadata_values (typed columns, one
per field type, per-value provenance/confidence/verification). documents
gains the nullable metadata_schema_id deferred since V0.1. Audit action and
target_type CHECKs widen for the ten metadata actions and schema targets.

Three composite FKs on metadata_values make cross-schema writes, type
lying, and schema replacement-with-values structurally impossible.

Downgrade REFUSES if any metadata exists — dropping tables that hold user
metadata is not reversibility (V0.2 precedent). The audit CHECK narrowing
likewise fails loudly if V0.3 events exist.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-12

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

_ACTION_CONSTRAINT = "ck_audit_events_action_valid"
_TARGET_CONSTRAINT = "ck_audit_events_target_type_valid"
_V02_ACTIONS = (
    "('FOLDER_CREATED','DOCUMENT_CREATED','ENTRY_RENAMED','ENTRY_MOVED',"
    "'DOCUMENT_VERSION_CREATED','DOCUMENT_VERSION_RESTORED')"
)
_V03_ACTIONS = (
    "('FOLDER_CREATED','DOCUMENT_CREATED','ENTRY_RENAMED','ENTRY_MOVED',"
    "'DOCUMENT_VERSION_CREATED','DOCUMENT_VERSION_RESTORED',"
    "'METADATA_SCHEMA_CREATED','METADATA_FIELD_ADDED','METADATA_FIELD_REMOVED',"
    "'METADATA_FIELD_RELABELED','METADATA_SCHEMA_PUBLISHED','METADATA_SCHEMA_RETIRED',"
    "'METADATA_SCHEMA_ASSIGNED','METADATA_VALUE_SET','METADATA_VALUE_VERIFIED',"
    "'METADATA_VALUE_DELETED')"
)


def upgrade() -> None:
    op.create_table(
        "metadata_schemas",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("state", sa.Text, nullable=False, server_default=sa.text("'draft'")),
        sa.Column(
            "created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("created_by", UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "name <> '' AND length(name) <= 255", name="ck_metadata_schemas_name_valid"
        ),
        sa.CheckConstraint(
            "state IN ('draft','active','retired')", name="ck_metadata_schemas_state_valid"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["principals.id"],
            name="fk_metadata_schemas_created_by_principals",
            ondelete="RESTRICT",
        ),
    )
    # schema names unique among live (non-retired) schemas, case-insensitive
    op.execute(
        "CREATE UNIQUE INDEX uq_metadata_schemas_live_name ON metadata_schemas (lower(name)) "
        "WHERE state <> 'retired'"
    )

    op.create_table(
        "metadata_fields",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("schema_id", UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.Text, nullable=False),
        sa.Column("label", sa.Text, nullable=False),
        sa.Column("field_type", sa.Text, nullable=False),
        sa.Column("required", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("position", sa.Integer, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column(
            "created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint(
            "key ~ '^[a-z][a-z0-9_]*$' AND length(key) <= 63",
            name="ck_metadata_fields_key_valid",
        ),
        sa.CheckConstraint("label <> ''", name="ck_metadata_fields_label_valid"),
        sa.CheckConstraint(
            "field_type IN ('text','integer','decimal','boolean','date','datetime')",
            name="ck_metadata_fields_field_type_valid",
        ),
        sa.UniqueConstraint("schema_id", "key", name="uq_metadata_fields_schema_id"),
        sa.UniqueConstraint("id", "schema_id", name="uq_metadata_fields_id_schema"),
        sa.UniqueConstraint("id", "field_type", name="uq_metadata_fields_id_type"),
        sa.ForeignKeyConstraint(
            ["schema_id"],
            ["metadata_schemas.id"],
            name="fk_metadata_fields_schema_id_metadata_schemas",
            ondelete="RESTRICT",
        ),
    )

    op.add_column(
        "documents", sa.Column("metadata_schema_id", UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_documents_metadata_schema_id_metadata_schemas",
        "documents",
        "metadata_schemas",
        ["metadata_schema_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    # composite-FK target for metadata_values (entry_id is the PK, so this
    # adds no real constraint — it exists to be referenced)
    op.create_unique_constraint(
        "uq_documents_entry_id_schema", "documents", ["entry_id", "metadata_schema_id"]
    )

    op.create_table(
        "metadata_values",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("document_id", UUID(as_uuid=True), nullable=False),
        sa.Column("schema_id", UUID(as_uuid=True), nullable=False),
        sa.Column("field_id", UUID(as_uuid=True), nullable=False),
        sa.Column("field_type", sa.Text, nullable=False),
        sa.Column("value_text", sa.Text, nullable=True),
        sa.Column("value_integer", sa.BigInteger, nullable=True),
        sa.Column("value_decimal", sa.Numeric, nullable=True),
        sa.Column("value_boolean", sa.Boolean, nullable=True),
        sa.Column("value_date", sa.Date, nullable=True),
        sa.Column("value_datetime", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("origin", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=True),
        sa.Column("confidence", sa.Numeric, nullable=True),
        sa.Column("verified_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("verified_by", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "set_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("set_by", UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "origin IN ('manual','extracted','imported','system')",
            name="ck_metadata_values_origin_valid",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_metadata_values_confidence_range"
        ),
        sa.CheckConstraint(
            "(verified_at IS NULL) = (verified_by IS NULL)",
            name="ck_metadata_values_verified_pair",
        ),
        sa.CheckConstraint(
            "num_nonnulls(value_text, value_integer, value_decimal, value_boolean, "
            "value_date, value_datetime) = 1 AND CASE field_type "
            "WHEN 'text' THEN value_text IS NOT NULL "
            "WHEN 'integer' THEN value_integer IS NOT NULL "
            "WHEN 'decimal' THEN value_decimal IS NOT NULL "
            "WHEN 'boolean' THEN value_boolean IS NOT NULL "
            "WHEN 'date' THEN value_date IS NOT NULL "
            "WHEN 'datetime' THEN value_datetime IS NOT NULL END",
            name="ck_metadata_values_value_matches_type",
        ),
        sa.UniqueConstraint("document_id", "field_id", name="uq_metadata_values_document_id"),
        # 1. value's schema must be the document's ASSIGNED schema; also blocks
        #    replacing/clearing the document's schema while values exist
        sa.ForeignKeyConstraint(
            ["document_id", "schema_id"],
            ["documents.entry_id", "documents.metadata_schema_id"],
            name="fk_metadata_values_document_schema",
        ),
        # 2. value's field must belong to the same schema
        sa.ForeignKeyConstraint(
            ["field_id", "schema_id"],
            ["metadata_fields.id", "metadata_fields.schema_id"],
            name="fk_metadata_values_field_schema",
        ),
        # 3. denormalized field_type provably matches the field definition
        sa.ForeignKeyConstraint(
            ["field_id", "field_type"],
            ["metadata_fields.id", "metadata_fields.field_type"],
            name="fk_metadata_values_field_type",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by"],
            ["principals.id"],
            name="fk_metadata_values_verified_by_principals",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["set_by"],
            ["principals.id"],
            name="fk_metadata_values_set_by_principals",
            ondelete="RESTRICT",
        ),
    )

    op.drop_constraint(_ACTION_CONSTRAINT, "audit_events", type_="check")
    op.create_check_constraint(_ACTION_CONSTRAINT, "audit_events", f"action IN {_V03_ACTIONS}")
    op.drop_constraint(_TARGET_CONSTRAINT, "audit_events", type_="check")
    op.create_check_constraint(
        _TARGET_CONSTRAINT, "audit_events", "target_type IN ('entry','schema')"
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM metadata_values)
               OR EXISTS (SELECT 1 FROM metadata_schemas) THEN
                RAISE EXCEPTION
                    'refusing to downgrade 0004: metadata exists and would be destroyed';
            END IF;
        END $$;
        """
    )
    op.drop_constraint(_TARGET_CONSTRAINT, "audit_events", type_="check")
    op.create_check_constraint(_TARGET_CONSTRAINT, "audit_events", "target_type IN ('entry')")
    op.drop_constraint(_ACTION_CONSTRAINT, "audit_events", type_="check")
    op.create_check_constraint(_ACTION_CONSTRAINT, "audit_events", f"action IN {_V02_ACTIONS}")
    op.drop_table("metadata_values")
    op.drop_constraint("uq_documents_entry_id_schema", "documents", type_="unique")
    op.drop_constraint(
        "fk_documents_metadata_schema_id_metadata_schemas", "documents", type_="foreignkey"
    )
    op.drop_column("documents", "metadata_schema_id")
    op.drop_table("metadata_fields")
    op.drop_table("metadata_schemas")
