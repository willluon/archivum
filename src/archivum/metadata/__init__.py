"""Metadata: schemas as data, typed values with provenance (ADR-0008/0009).

MetadataService is the only write path to metadata state. Schema lifecycle
operations serialize on the metadata_schemas row lock; document metadata
operations serialize on the documents row lock (the same lock content
versioning uses, so metadata and version writes never interleave).
"""

import datetime
import re
import uuid
from decimal import Decimal
from decimal import InvalidOperation as DecimalInvalid
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import Connection, Engine

from archivum.audit import record_event
from archivum.db.concurrency import bump_revision
from archivum.db.tables import (
    FIELD_TYPES,
    VALUE_ORIGINS,
    documents,
    entries,
    metadata_fields,
    metadata_schemas,
    metadata_values,
)
from archivum.domain import (
    DuplicateFieldKey,
    DuplicateSchemaName,
    EntryNotFound,
    FieldNotFound,
    InvalidFieldKey,
    InvalidMetadataValue,
    InvalidOperation,
    MetadataNotAssigned,
    NotADocument,
    SchemaAssignmentError,
    SchemaNotFound,
    SchemaStateError,
    new_id,
)

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_VALUE_COLUMNS = {
    "text": "value_text",
    "integer": "value_integer",
    "decimal": "value_decimal",
    "boolean": "value_boolean",
    "date": "value_date",
    "datetime": "value_datetime",
}


def parse_value(field_type: str, raw: Any):
    """Strictly parse a raw (usually string) value for a field type.

    The one place coercion lives; everything invalid dies here with
    InvalidMetadataValue before any transaction begins.
    """
    if raw is None:
        raise InvalidMetadataValue("value must not be None")
    if field_type == "text":
        value = str(raw)
        if not value.strip():
            raise InvalidMetadataValue("text value must not be empty")
        return value
    if field_type == "integer":
        if isinstance(raw, bool):
            raise InvalidMetadataValue("boolean is not an integer")
        if isinstance(raw, int):
            return raw
        text_value = str(raw).strip()
        if not re.fullmatch(r"[+-]?\d+", text_value):
            raise InvalidMetadataValue(f"not an integer: {raw!r}")
        return int(text_value)
    if field_type == "decimal":
        if isinstance(raw, bool):
            raise InvalidMetadataValue("boolean is not a decimal")
        try:
            value = Decimal(str(raw).strip())
        except DecimalInvalid:
            raise InvalidMetadataValue(f"not a decimal: {raw!r}") from None
        # PostgreSQL numeric accepts NaN — reject it here, deliberately
        if not value.is_finite():
            raise InvalidMetadataValue("decimal must be finite")
        return value
    if field_type == "boolean":
        if isinstance(raw, bool):
            return raw
        text_value = str(raw).strip().lower()
        if text_value == "true":
            return True
        if text_value == "false":
            return False
        raise InvalidMetadataValue(f"not a boolean (use 'true'/'false'): {raw!r}")
    if field_type == "date":
        if isinstance(raw, datetime.date) and not isinstance(raw, datetime.datetime):
            return raw
        try:
            return datetime.date.fromisoformat(str(raw).strip())
        except ValueError:
            raise InvalidMetadataValue(f"not an ISO date (YYYY-MM-DD): {raw!r}") from None
    if field_type == "datetime":
        if isinstance(raw, datetime.datetime):
            value = raw
        else:
            try:
                value = datetime.datetime.fromisoformat(str(raw).strip())
            except ValueError:
                raise InvalidMetadataValue(f"not an ISO datetime: {raw!r}") from None
        if value.tzinfo is None:
            raise InvalidMetadataValue("datetime must include a timezone offset")
        return value
    raise InvalidMetadataValue(f"unknown field type: {field_type}")


class MetadataService:
    def __init__(self, engine: Engine):
        self.engine = engine

    # ── schema lifecycle ──────────────────────────────────────────────────

    def create_schema(
        self, actor_id: uuid.UUID, name: str, description: str | None = None
    ) -> uuid.UUID:
        if not name or not name.strip() or len(name) > 255:
            raise InvalidMetadataValue("schema name must be 1-255 characters")
        with self.engine.begin() as conn:
            live = conn.execute(
                select(metadata_schemas.c.id).where(
                    func.lower(metadata_schemas.c.name) == name.lower(),
                    metadata_schemas.c.state != "retired",
                )
            ).first()
            if live is not None:
                raise DuplicateSchemaName(f"a live schema named {name!r} already exists")
            schema_id = new_id()
            conn.execute(
                metadata_schemas.insert().values(
                    id=schema_id, name=name, description=description, created_by=actor_id
                )
            )
            record_event(
                conn,
                actor_id=actor_id,
                action="METADATA_SCHEMA_CREATED",
                target_type="schema",
                target_id=schema_id,
                details={"name": name},
            )
        return schema_id

    def add_field(
        self,
        actor_id: uuid.UUID,
        schema_ref: uuid.UUID | str,
        key: str,
        field_type: str,
        *,
        label: str | None = None,
        required: bool = False,
        description: str | None = None,
    ) -> uuid.UUID:
        if not _KEY_RE.fullmatch(key) or len(key) > 63:
            raise InvalidFieldKey(
                f"field key must match ^[a-z][a-z0-9_]*$ (max 63 chars): {key!r}"
            )
        if field_type not in FIELD_TYPES:
            raise InvalidMetadataValue(
                f"unknown field type {field_type!r} (choose from {', '.join(FIELD_TYPES)})"
            )
        with self.engine.begin() as conn:
            schema = self._schema(conn, schema_ref, for_update=True)
            if schema.state != "draft":
                raise SchemaStateError(
                    f"schema {schema.name!r} is {schema.state}; fields can only be "
                    "added to draft schemas (create a new schema for structural changes)"
                )
            duplicate = conn.execute(
                select(metadata_fields.c.id).where(
                    metadata_fields.c.schema_id == schema.id, metadata_fields.c.key == key
                )
            ).first()
            if duplicate is not None:
                raise DuplicateFieldKey(f"schema {schema.name!r} already has a field {key!r}")
            position = conn.execute(
                select(func.coalesce(func.max(metadata_fields.c.position), 0) + 1).where(
                    metadata_fields.c.schema_id == schema.id
                )
            ).scalar_one()
            field_id = new_id()
            conn.execute(
                metadata_fields.insert().values(
                    id=field_id,
                    schema_id=schema.id,
                    key=key,
                    label=label or key.replace("_", " ").title(),
                    field_type=field_type,
                    required=required,
                    position=position,
                    description=description,
                )
            )
            record_event(
                conn,
                actor_id=actor_id,
                action="METADATA_FIELD_ADDED",
                target_type="schema",
                target_id=schema.id,
                details={
                    "field_id": str(field_id),
                    "key": key,
                    "field_type": field_type,
                    "required": required,
                },
            )
        return field_id

    def remove_field(self, actor_id: uuid.UUID, schema_ref, key: str) -> None:
        with self.engine.begin() as conn:
            schema = self._schema(conn, schema_ref, for_update=True)
            if schema.state != "draft":
                raise SchemaStateError(
                    f"schema {schema.name!r} is {schema.state}; fields can only be "
                    "removed from draft schemas"
                )
            field = self._field(conn, schema, key)
            conn.execute(delete(metadata_fields).where(metadata_fields.c.id == field.id))
            record_event(
                conn,
                actor_id=actor_id,
                action="METADATA_FIELD_REMOVED",
                target_type="schema",
                target_id=schema.id,
                details={"field_id": str(field.id), "key": key, "field_type": field.field_type},
            )

    def relabel_field(self, actor_id: uuid.UUID, schema_ref, key: str, new_label: str) -> None:
        if not new_label or not new_label.strip():
            raise InvalidMetadataValue("label must not be empty")
        with self.engine.begin() as conn:
            schema = self._schema(conn, schema_ref, for_update=True)
            field = self._field(conn, schema, key)
            conn.execute(
                update(metadata_fields)
                .where(metadata_fields.c.id == field.id)
                .values(label=new_label)
            )
            record_event(
                conn,
                actor_id=actor_id,
                action="METADATA_FIELD_RELABELED",
                target_type="schema",
                target_id=schema.id,
                details={
                    "field_id": str(field.id),
                    "key": key,
                    "old_label": field.label,
                    "new_label": new_label,
                },
            )

    def publish_schema(self, actor_id: uuid.UUID, schema_ref) -> None:
        with self.engine.begin() as conn:
            schema = self._schema(conn, schema_ref, for_update=True)
            if schema.state != "draft":
                raise SchemaStateError(f"schema {schema.name!r} is already {schema.state}")
            field_count = conn.execute(
                select(func.count()).where(metadata_fields.c.schema_id == schema.id)
            ).scalar_one()
            if field_count == 0:
                raise SchemaStateError("cannot publish a schema with no fields")
            conn.execute(
                update(metadata_schemas)
                .where(metadata_schemas.c.id == schema.id)
                .values(state="active")
            )
            record_event(
                conn,
                actor_id=actor_id,
                action="METADATA_SCHEMA_PUBLISHED",
                target_type="schema",
                target_id=schema.id,
                details={"name": schema.name, "field_count": field_count},
            )

    def retire_schema(self, actor_id: uuid.UUID, schema_ref) -> None:
        with self.engine.begin() as conn:
            schema = self._schema(conn, schema_ref, for_update=True)
            if schema.state != "active":
                raise SchemaStateError("only active schemas can be retired")
            conn.execute(
                update(metadata_schemas)
                .where(metadata_schemas.c.id == schema.id)
                .values(state="retired")
            )
            record_event(
                conn,
                actor_id=actor_id,
                action="METADATA_SCHEMA_RETIRED",
                target_type="schema",
                target_id=schema.id,
                details={"name": schema.name},
            )

    def get_schema(self, schema_ref) -> dict:
        with self.engine.connect() as conn:
            schema = self._schema(conn, schema_ref)
            fields = conn.execute(
                select(metadata_fields)
                .where(metadata_fields.c.schema_id == schema.id)
                .order_by(metadata_fields.c.position)
            ).all()
        return {
            "id": schema.id,
            "name": schema.name,
            "description": schema.description,
            "state": schema.state,
            "fields": [
                {
                    "id": f.id,
                    "key": f.key,
                    "label": f.label,
                    "field_type": f.field_type,
                    "required": f.required,
                    "position": f.position,
                    "description": f.description,
                }
                for f in fields
            ],
        }

    def list_schemas(self) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(
                    metadata_schemas.c.id,
                    metadata_schemas.c.name,
                    metadata_schemas.c.description,
                    metadata_schemas.c.state,
                    metadata_schemas.c.created_at,
                ).order_by(func.lower(metadata_schemas.c.name), metadata_schemas.c.created_at)
            ).all()
        return [dict(r._mapping) for r in rows]

    # ── document metadata ─────────────────────────────────────────────────

    def assign_schema(
        self,
        actor_id: uuid.UUID,
        document_id: uuid.UUID,
        schema_ref,
        *,
        expected_revision: int | None = None,
    ) -> int:
        with self.engine.begin() as conn:
            new_revision = bump_revision(conn, document_id, expected_revision)
            current_schema_id = self._locked_document_schema(conn, document_id)
            schema = self._schema(conn, schema_ref)
            if schema.state != "active":
                raise SchemaStateError(
                    f"schema {schema.name!r} is {schema.state}; only active schemas "
                    "can be assigned"
                )
            if current_schema_id is not None and current_schema_id != schema.id:
                value_count = conn.execute(
                    select(func.count()).where(metadata_values.c.document_id == document_id)
                ).scalar_one()
                if value_count:
                    raise SchemaAssignmentError(
                        f"document has {value_count} metadata value(s) under its current "
                        "schema; delete them before assigning a different schema"
                    )
            conn.execute(
                update(documents)
                .where(documents.c.entry_id == document_id)
                .values(metadata_schema_id=schema.id)
            )
            record_event(
                conn,
                actor_id=actor_id,
                action="METADATA_SCHEMA_ASSIGNED",
                target_id=document_id,
                details={"schema_id": str(schema.id), "schema_name": schema.name},
            )
        return new_revision

    def set_metadata_value(
        self,
        actor_id: uuid.UUID,
        document_id: uuid.UUID,
        field_key: str,
        raw_value,
        *,
        origin: str = "manual",
        source: str | None = None,
        confidence=None,
        expected_revision: int | None = None,
    ) -> dict:
        if origin not in VALUE_ORIGINS:
            raise InvalidMetadataValue(
                f"unknown origin {origin!r} (choose from {', '.join(VALUE_ORIGINS)})"
            )
        confidence = self._parse_confidence(confidence)
        with self.engine.begin() as conn:
            new_revision = bump_revision(conn, document_id, expected_revision)
            schema_id = self._locked_document_schema(conn, document_id)
            if schema_id is None:
                raise MetadataNotAssigned(f"document {document_id} has no metadata schema")
            field = self._field_by_schema_id(conn, schema_id, field_key)
            value = parse_value(field.field_type, raw_value)
            # Manual values are born verified — a human typing IS confirmation.
            # Machine values are born unverified; any overwrite resets (ADR-0008).
            verified_by = actor_id if origin == "manual" else None
            verified_at = func.now() if origin == "manual" else None
            value_columns = {col: None for col in _VALUE_COLUMNS.values()}
            value_columns[_VALUE_COLUMNS[field.field_type]] = value
            existing = conn.execute(
                select(metadata_values.c.id).where(
                    metadata_values.c.document_id == document_id,
                    metadata_values.c.field_id == field.id,
                )
            ).scalar_one_or_none()
            if existing is not None:
                value_id, replaced = existing, True
                conn.execute(
                    update(metadata_values)
                    .where(metadata_values.c.id == value_id)
                    .values(
                        origin=origin,
                        source=source,
                        confidence=confidence,
                        verified_at=verified_at,
                        verified_by=verified_by,
                        set_at=func.now(),
                        set_by=actor_id,
                        **value_columns,
                    )
                )
            else:
                value_id, replaced = new_id(), False
                conn.execute(
                    metadata_values.insert().values(
                        id=value_id,
                        document_id=document_id,
                        schema_id=schema_id,
                        field_id=field.id,
                        field_type=field.field_type,
                        origin=origin,
                        source=source,
                        confidence=confidence,
                        verified_at=verified_at,
                        verified_by=verified_by,
                        set_by=actor_id,
                        **value_columns,
                    )
                )
            # Audit records WHO changed WHICH field and how it originated —
            # never the value itself (ADR-0008).
            record_event(
                conn,
                actor_id=actor_id,
                action="METADATA_VALUE_SET",
                target_id=document_id,
                details={
                    "value_id": str(value_id),
                    "field_id": str(field.id),
                    "field_key": field_key,
                    "origin": origin,
                    "source": source,
                    "confidence": float(confidence) if confidence is not None else None,
                    "replaced": replaced,
                },
            )
        return {"value_id": value_id, "replaced": replaced, "revision": new_revision}

    def verify_metadata_value(
        self,
        actor_id: uuid.UUID,
        document_id: uuid.UUID,
        field_key: str,
        *,
        expected_revision: int | None = None,
    ) -> int:
        with self.engine.begin() as conn:
            new_revision = bump_revision(conn, document_id, expected_revision)
            schema_id = self._locked_document_schema(conn, document_id)
            if schema_id is None:
                raise MetadataNotAssigned(f"document {document_id} has no metadata schema")
            field = self._field_by_schema_id(conn, schema_id, field_key)
            row = conn.execute(
                select(metadata_values.c.id, metadata_values.c.origin,
                       metadata_values.c.confidence).where(
                    metadata_values.c.document_id == document_id,
                    metadata_values.c.field_id == field.id,
                )
            ).one_or_none()
            if row is None:
                raise InvalidOperation(f"no value set for field {field_key!r}")
            conn.execute(
                update(metadata_values)
                .where(metadata_values.c.id == row.id)
                .values(verified_at=func.now(), verified_by=actor_id)
            )
            record_event(
                conn,
                actor_id=actor_id,
                action="METADATA_VALUE_VERIFIED",
                target_id=document_id,
                details={
                    "value_id": str(row.id),
                    "field_id": str(field.id),
                    "field_key": field_key,
                    "origin": row.origin,
                    "confidence": float(row.confidence) if row.confidence is not None else None,
                },
            )
        return new_revision

    def delete_metadata_value(
        self,
        actor_id: uuid.UUID,
        document_id: uuid.UUID,
        field_key: str,
        *,
        expected_revision: int | None = None,
    ) -> int:
        with self.engine.begin() as conn:
            new_revision = bump_revision(conn, document_id, expected_revision)
            schema_id = self._locked_document_schema(conn, document_id)
            if schema_id is None:
                raise MetadataNotAssigned(f"document {document_id} has no metadata schema")
            field = self._field_by_schema_id(conn, schema_id, field_key)
            row = conn.execute(
                select(metadata_values.c.id).where(
                    metadata_values.c.document_id == document_id,
                    metadata_values.c.field_id == field.id,
                )
            ).scalar_one_or_none()
            if row is None:
                raise InvalidOperation(f"no value set for field {field_key!r}")
            conn.execute(delete(metadata_values).where(metadata_values.c.id == row))
            record_event(
                conn,
                actor_id=actor_id,
                action="METADATA_VALUE_DELETED",
                target_id=document_id,
                details={
                    "value_id": str(row),
                    "field_id": str(field.id),
                    "field_key": field_key,
                },
            )
        return new_revision

    def get_metadata(self, document_id: uuid.UUID) -> dict:
        with self.engine.connect() as conn:
            self._require_document(conn, document_id)
            schema_id = conn.execute(
                select(documents.c.metadata_schema_id).where(
                    documents.c.entry_id == document_id
                )
            ).scalar_one()
            if schema_id is None:
                return {"schema": None, "values": [], "missing_required": [], "complete": True}
            schema = self._schema(conn, schema_id)
            fields = conn.execute(
                select(metadata_fields)
                .where(metadata_fields.c.schema_id == schema_id)
                .order_by(metadata_fields.c.position)
            ).all()
            rows = {
                r.field_id: r
                for r in conn.execute(
                    select(metadata_values).where(
                        metadata_values.c.document_id == document_id
                    )
                ).all()
            }
        values, missing_required = [], []
        for field in fields:
            row = rows.get(field.id)
            if row is None:
                if field.required:
                    missing_required.append(field.key)
                continue
            values.append(
                {
                    "value_id": row.id,
                    "key": field.key,
                    "label": field.label,
                    "field_type": field.field_type,
                    "value": getattr(row, _VALUE_COLUMNS[field.field_type]),
                    "origin": row.origin,
                    "source": row.source,
                    "confidence": row.confidence,
                    "verified_at": row.verified_at,
                    "verified_by": row.verified_by,
                    "set_at": row.set_at,
                    "set_by": row.set_by,
                }
            )
        return {
            "schema": {"id": schema.id, "name": schema.name, "state": schema.state},
            "values": values,
            "missing_required": missing_required,
            "complete": not missing_required,
        }

    # ── internals ─────────────────────────────────────────────────────────

    def _schema(self, conn: Connection, ref, for_update: bool = False):
        stmt = select(metadata_schemas)
        if isinstance(ref, uuid.UUID):
            stmt = stmt.where(metadata_schemas.c.id == ref)
        else:
            stmt = stmt.where(
                func.lower(metadata_schemas.c.name) == str(ref).lower(),
                metadata_schemas.c.state != "retired",
            )
        if for_update:
            stmt = stmt.with_for_update()
        row = conn.execute(stmt).one_or_none()
        if row is None:
            raise SchemaNotFound(f"no schema {ref!r}")
        return row

    def _field(self, conn: Connection, schema, key: str):
        return self._field_by_schema_id(conn, schema.id, key)

    def _field_by_schema_id(self, conn: Connection, schema_id: uuid.UUID, key: str):
        row = conn.execute(
            select(metadata_fields).where(
                metadata_fields.c.schema_id == schema_id, metadata_fields.c.key == key
            )
        ).one_or_none()
        if row is None:
            raise FieldNotFound(f"the assigned schema has no field {key!r}")
        return row

    def _require_document(self, conn: Connection, document_id: uuid.UUID) -> None:
        entry = conn.execute(
            select(entries.c.entry_type).where(
                entries.c.id == document_id, entries.c.state == "active"
            )
        ).scalar_one_or_none()
        if entry is None:
            raise EntryNotFound(str(document_id))
        if entry != "document":
            raise NotADocument(f"{document_id} is not a document")

    def _locked_document_schema(self, conn: Connection, document_id: uuid.UUID):
        self._require_document(conn, document_id)
        return conn.execute(
            select(documents.c.metadata_schema_id)
            .where(documents.c.entry_id == document_id)
            .with_for_update()
        ).scalar_one()

    @staticmethod
    def _parse_confidence(confidence):
        if confidence is None:
            return None
        try:
            value = Decimal(str(confidence))
        except DecimalInvalid:
            raise InvalidMetadataValue(f"confidence must be a number: {confidence!r}") from None
        if not (0 <= value <= 1):
            raise InvalidMetadataValue(f"confidence must be between 0 and 1: {confidence}")
        return value
