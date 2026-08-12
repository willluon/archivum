import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from archivum.api.deps import get_actor, get_meta
from archivum.api.dto import FieldInfo, SchemaDetail, SchemaSummary
from archivum.api.problems import ApiProblem
from archivum.db.tables import FIELD_TYPES

router = APIRouter(prefix="/schemas", tags=["schemas"])


class SchemaCreate(BaseModel):
    name: str
    description: str | None = None


class FieldCreate(BaseModel):
    key: str
    field_type: str
    label: str | None = None
    required: bool = False
    description: str | None = None


class FieldPatch(BaseModel):
    label: str


def _detail(msvc, schema_id) -> SchemaDetail:
    return SchemaDetail(**msvc.get_schema(schema_id))


@router.post("", status_code=201, summary="Create a draft schema")
def create_schema(
    body: SchemaCreate, msvc=Depends(get_meta), actor=Depends(get_actor)
) -> SchemaDetail:
    schema_id = msvc.create_schema(actor, body.name, description=body.description)
    return _detail(msvc, schema_id)


@router.get("", summary="List schemas")
def list_schemas(msvc=Depends(get_meta)) -> list[SchemaSummary]:
    return [SchemaSummary(**s) for s in msvc.list_schemas()]


@router.get("/{schema_id}", summary="Get a schema with its fields")
def get_schema(schema_id: uuid.UUID, msvc=Depends(get_meta)) -> SchemaDetail:
    return _detail(msvc, schema_id)


@router.post("/{schema_id}/fields", status_code=201, summary="Add a field (draft only)")
def add_field(
    schema_id: uuid.UUID,
    body: FieldCreate,
    msvc=Depends(get_meta),
    actor=Depends(get_actor),
) -> FieldInfo:
    if body.field_type not in FIELD_TYPES:
        raise ApiProblem(
            422,
            "invalid_metadata_value",
            f"unknown field type {body.field_type!r} (choose from {', '.join(FIELD_TYPES)})",
        )
    field_id = msvc.add_field(
        actor, schema_id, body.key, body.field_type,
        label=body.label, required=body.required, description=body.description,
    )
    field = next(f for f in msvc.get_schema(schema_id)["fields"] if f["id"] == field_id)
    return FieldInfo(**field)


@router.patch("/{schema_id}/fields/{key}", summary="Relabel a field (display only)")
def relabel_field(
    schema_id: uuid.UUID,
    key: str,
    body: FieldPatch,
    msvc=Depends(get_meta),
    actor=Depends(get_actor),
) -> FieldInfo:
    msvc.relabel_field(actor, schema_id, key, body.label)
    field = next(f for f in msvc.get_schema(schema_id)["fields"] if f["key"] == key)
    return FieldInfo(**field)


@router.delete("/{schema_id}/fields/{key}", status_code=204, summary="Remove a field (draft only)")
def remove_field(
    schema_id: uuid.UUID, key: str, msvc=Depends(get_meta), actor=Depends(get_actor)
) -> None:
    msvc.remove_field(actor, schema_id, key)


@router.post("/{schema_id}/publish", summary="Publish a draft schema (freezes structure)")
def publish_schema(
    schema_id: uuid.UUID, msvc=Depends(get_meta), actor=Depends(get_actor)
) -> SchemaDetail:
    msvc.publish_schema(actor, schema_id)
    return _detail(msvc, schema_id)


@router.post("/{schema_id}/retire", summary="Retire an active schema")
def retire_schema(
    schema_id: uuid.UUID, msvc=Depends(get_meta), actor=Depends(get_actor)
) -> SchemaDetail:
    msvc.retire_schema(actor, schema_id)
    return _detail(msvc, schema_id)
