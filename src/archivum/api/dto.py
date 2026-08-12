"""Public response DTOs. Explicit fields only — service dicts carry internal
detail (storage keys, blob rows) that must never serialize; extra keys are
ignored by construction. sha256 is public integrity metadata (ADR-0010).
"""

import datetime
import uuid
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

Sha256Hex = Annotated[
    str, BeforeValidator(lambda v: v.hex() if isinstance(v, (bytes, bytearray)) else v)
]


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CurrentVersion(_Base):
    version_number: int
    mime_type: str
    size_bytes: int
    sha256: Sha256Hex


class SchemaRef(_Base):
    id: uuid.UUID
    name: str
    state: str | None = None


class FolderDetail(_Base):
    id: uuid.UUID
    title: str
    parent_id: uuid.UUID | None
    revision: int
    created_at: datetime.datetime
    created_by: uuid.UUID


class DocumentDetail(_Base):
    id: uuid.UUID
    title: str
    parent_id: uuid.UUID
    revision: int
    created_at: datetime.datetime
    created_by: uuid.UUID
    current_version: CurrentVersion
    schema_: SchemaRef | None = Field(default=None, serialization_alias="schema")
    metadata_complete: bool


class VersionInfo(_Base):
    version_number: int
    mime_type: str
    size_bytes: int
    sha256: Sha256Hex
    original_filename: str | None
    change_note: str | None
    created_at: datetime.datetime
    created_by: uuid.UUID
    is_current: bool


class ChildInfo(_Base):
    id: uuid.UUID
    type: str
    title: str


class ChildPage(_Base):
    items: list[ChildInfo]
    total: int
    limit: int
    offset: int


class FieldInfo(_Base):
    id: uuid.UUID
    key: str
    label: str
    field_type: str
    required: bool
    position: int
    description: str | None


class SchemaSummary(_Base):
    id: uuid.UUID
    name: str
    description: str | None
    state: str
    created_at: datetime.datetime


class SchemaDetail(_Base):
    id: uuid.UUID
    name: str
    description: str | None
    state: str
    fields: list[FieldInfo]


class MetadataValueInfo(_Base):
    key: str
    label: str
    field_type: str
    value: Any
    origin: str
    source: str | None
    confidence: str | None
    verified: bool
    verified_at: datetime.datetime | None
    verified_by: uuid.UUID | None
    set_at: datetime.datetime
    set_by: uuid.UUID


class MetadataState(_Base):
    schema_: SchemaRef | None = Field(default=None, serialization_alias="schema")
    values: list[MetadataValueInfo]
    missing_required: list[str]
    complete: bool


class AuditEventInfo(_Base):
    id: int
    occurred_at: datetime.datetime
    actor_id: uuid.UUID
    action: str
    details: dict


class AuditPage(_Base):
    items: list[AuditEventInfo]
    total: int
    limit: int
    offset: int


class ResolveResult(_Base):
    id: uuid.UUID
    type: str


class VerificationResult(_Base):
    versions: dict[int, bool]
    all_ok: bool
