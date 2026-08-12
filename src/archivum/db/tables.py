"""Kernel table definitions (SQLAlchemy Core), mirrored by migration 0002.

Constraint names follow the MetaData naming convention in archivum.db;
check-constraint short names expand to ck_<table>_<name>. See ADR-0005 and
ADR-0006 for the design; the generated parent_type / entry_type columns are
the composite-FK trick that lets PostgreSQL enforce "parent must be a
folder" and "only document entries have document state".
"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Computed,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Integer,
    Numeric,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, TIMESTAMP, UUID

from archivum.db import metadata

principals = Table(
    "principals",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("principal_type", Text, nullable=False),
    Column("display_name", Text, nullable=False),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("principal_type IN ('user','service','system')", name="type_valid"),
    CheckConstraint("display_name <> ''", name="display_name_nonempty"),
)

blobs = Table(
    "blobs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("sha256", BYTEA, nullable=False, unique=True),
    Column("size_bytes", BigInteger, nullable=False),
    Column("storage_key", Text, nullable=False, unique=True),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("octet_length(sha256) = 32", name="sha256_len"),
    CheckConstraint("size_bytes >= 0", name="size_nonneg"),
)

entries = Table(
    "entries",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("entry_type", Text, nullable=False),
    Column("title", Text, nullable=False),
    Column("parent_id", UUID(as_uuid=True), nullable=True),
    Column(
        "parent_type",
        Text,
        Computed("CASE WHEN parent_id IS NULL THEN NULL ELSE 'folder' END", persisted=True),
        nullable=True,
    ),
    Column("state", Text, nullable=False, server_default=text("'active'")),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column(
        "created_by",
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("entry_type IN ('folder','document')", name="entry_type_valid"),
    CheckConstraint("title <> '' AND length(title) <= 255", name="title_valid"),
    CheckConstraint("state IN ('active','deleted')", name="state_valid"),
    UniqueConstraint("id", "entry_type"),
    ForeignKeyConstraint(
        ["parent_id", "parent_type"],
        ["entries.id", "entries.entry_type"],
        ondelete="RESTRICT",
    ),
    Index(
        "uq_entries_sibling_title",
        "parent_id",
        text("lower(title)"),
        unique=True,
        postgresql_where=text("state = 'active'"),
    ),
    Index(
        "uq_entries_single_root",
        text("(true)"),
        unique=True,
        postgresql_where=text("parent_id IS NULL"),
    ),
    Index("ix_entries_parent_active", "parent_id", postgresql_where=text("state = 'active'")),
)

documents = Table(
    "documents",
    metadata,
    Column("entry_id", UUID(as_uuid=True), primary_key=True),
    Column("entry_type", Text, Computed("'document'", persisted=True), nullable=False),
    Column("current_version_id", UUID(as_uuid=True), nullable=True),
    Column(
        "metadata_schema_id",
        UUID(as_uuid=True),
        ForeignKey("metadata_schemas.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    UniqueConstraint("entry_id", "metadata_schema_id", name="uq_documents_entry_id_schema"),
    ForeignKeyConstraint(
        ["entry_id", "entry_type"],
        ["entries.id", "entries.entry_type"],
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["current_version_id", "entry_id"],
        ["document_versions.id", "document_versions.document_id"],
        name="fk_documents_current_version",
        use_alter=True,
    ),
)

FIELD_TYPES = ("text", "integer", "decimal", "boolean", "date", "datetime")
VALUE_ORIGINS = ("manual", "extracted", "imported", "system")

metadata_schemas = Table(
    "metadata_schemas",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=True),
    Column("state", Text, nullable=False, server_default=text("'draft'")),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column(
        "created_by",
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    CheckConstraint("name <> '' AND length(name) <= 255", name="name_valid"),
    CheckConstraint("state IN ('draft','active','retired')", name="state_valid"),
    Index(
        "uq_metadata_schemas_live_name",
        text("lower(name)"),
        unique=True,
        postgresql_where=text("state <> 'retired'"),
    ),
)

metadata_fields = Table(
    "metadata_fields",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "schema_id",
        UUID(as_uuid=True),
        ForeignKey("metadata_schemas.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("key", Text, nullable=False),
    Column("label", Text, nullable=False),
    Column("field_type", Text, nullable=False),
    Column("required", Boolean, nullable=False, server_default=text("false")),
    Column("position", Integer, nullable=False),
    Column("description", Text, nullable=True),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("key ~ '^[a-z][a-z0-9_]*$' AND length(key) <= 63", name="key_valid"),
    CheckConstraint("label <> ''", name="label_valid"),
    CheckConstraint(
        "field_type IN ('text','integer','decimal','boolean','date','datetime')",
        name="field_type_valid",
    ),
    UniqueConstraint("schema_id", "key"),
    UniqueConstraint("id", "schema_id", name="uq_metadata_fields_id_schema"),
    UniqueConstraint("id", "field_type", name="uq_metadata_fields_id_type"),
)

metadata_values = Table(
    "metadata_values",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("document_id", UUID(as_uuid=True), nullable=False),
    Column("schema_id", UUID(as_uuid=True), nullable=False),
    Column("field_id", UUID(as_uuid=True), nullable=False),
    Column("field_type", Text, nullable=False),
    Column("value_text", Text, nullable=True),
    Column("value_integer", BigInteger, nullable=True),
    Column("value_decimal", Numeric, nullable=True),
    Column("value_boolean", Boolean, nullable=True),
    Column("value_date", Date, nullable=True),
    Column("value_datetime", TIMESTAMP(timezone=True), nullable=True),
    Column("origin", Text, nullable=False),
    Column("source", Text, nullable=True),
    Column("confidence", Numeric, nullable=True),
    Column("verified_at", TIMESTAMP(timezone=True), nullable=True),
    Column(
        "verified_by",
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    Column("set_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column(
        "set_by",
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    CheckConstraint(
        "origin IN ('manual','extracted','imported','system')", name="origin_valid"
    ),
    CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    CheckConstraint("(verified_at IS NULL) = (verified_by IS NULL)", name="verified_pair"),
    CheckConstraint(
        "num_nonnulls(value_text, value_integer, value_decimal, value_boolean, "
        "value_date, value_datetime) = 1 AND CASE field_type "
        "WHEN 'text' THEN value_text IS NOT NULL "
        "WHEN 'integer' THEN value_integer IS NOT NULL "
        "WHEN 'decimal' THEN value_decimal IS NOT NULL "
        "WHEN 'boolean' THEN value_boolean IS NOT NULL "
        "WHEN 'date' THEN value_date IS NOT NULL "
        "WHEN 'datetime' THEN value_datetime IS NOT NULL END",
        name="value_matches_type",
    ),
    UniqueConstraint("document_id", "field_id"),
    ForeignKeyConstraint(
        ["document_id", "schema_id"],
        ["documents.entry_id", "documents.metadata_schema_id"],
        name="fk_metadata_values_document_schema",
    ),
    ForeignKeyConstraint(
        ["field_id", "schema_id"],
        ["metadata_fields.id", "metadata_fields.schema_id"],
        name="fk_metadata_values_field_schema",
    ),
    ForeignKeyConstraint(
        ["field_id", "field_type"],
        ["metadata_fields.id", "metadata_fields.field_type"],
        name="fk_metadata_values_field_type",
    ),
)

document_versions = Table(
    "document_versions",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column(
        "document_id",
        UUID(as_uuid=True),
        ForeignKey("documents.entry_id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("version_number", Integer, nullable=False),
    Column(
        "blob_id", UUID(as_uuid=True), ForeignKey("blobs.id", ondelete="RESTRICT"), nullable=False
    ),
    Column("mime_type", Text, nullable=False),
    Column("original_filename", Text, nullable=True),
    Column("change_note", Text, nullable=True),
    Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column(
        "created_by",
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    CheckConstraint("version_number >= 1", name="version_number_positive"),
    UniqueConstraint("document_id", "version_number"),
    UniqueConstraint("id", "document_id"),
)

AUDIT_ACTIONS = (
    "FOLDER_CREATED",
    "DOCUMENT_CREATED",
    "ENTRY_RENAMED",
    "ENTRY_MOVED",
    "DOCUMENT_VERSION_CREATED",
    "DOCUMENT_VERSION_RESTORED",
    "METADATA_SCHEMA_CREATED",
    "METADATA_FIELD_ADDED",
    "METADATA_FIELD_REMOVED",
    "METADATA_FIELD_RELABELED",
    "METADATA_SCHEMA_PUBLISHED",
    "METADATA_SCHEMA_RETIRED",
    "METADATA_SCHEMA_ASSIGNED",
    "METADATA_VALUE_SET",
    "METADATA_VALUE_VERIFIED",
    "METADATA_VALUE_DELETED",
)

AUDIT_TARGET_TYPES = ("entry", "schema")

audit_events = Table(
    "audit_events",
    metadata,
    Column("id", BigInteger, Identity(always=True), primary_key=True),
    Column("occurred_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column(
        "actor_id",
        UUID(as_uuid=True),
        ForeignKey("principals.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("action", Text, nullable=False),
    Column("target_type", Text, nullable=False, server_default=text("'entry'")),
    # Deliberately NO foreign key: audit history must survive its target,
    # including future purge (ADR-0004, ADR-0006 §4).
    Column("target_id", UUID(as_uuid=True), nullable=False),
    Column("details", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    CheckConstraint(
        "action IN ('FOLDER_CREATED','DOCUMENT_CREATED','ENTRY_RENAMED','ENTRY_MOVED',"
        "'DOCUMENT_VERSION_CREATED','DOCUMENT_VERSION_RESTORED',"
        "'METADATA_SCHEMA_CREATED','METADATA_FIELD_ADDED','METADATA_FIELD_REMOVED',"
        "'METADATA_FIELD_RELABELED','METADATA_SCHEMA_PUBLISHED','METADATA_SCHEMA_RETIRED',"
        "'METADATA_SCHEMA_ASSIGNED','METADATA_VALUE_SET','METADATA_VALUE_VERIFIED',"
        "'METADATA_VALUE_DELETED')",
        name="action_valid",
    ),
    CheckConstraint("target_type IN ('entry','schema')", name="target_type_valid"),
    Index("ix_audit_events_target", "target_id", "id"),
)
