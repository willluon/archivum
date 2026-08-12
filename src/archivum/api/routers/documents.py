import os
import uuid
from decimal import Decimal
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from archivum.api.deps import get_actor, get_meta, get_repo, require_if_match
from archivum.api.dto import (
    AuditEventInfo,
    AuditPage,
    CurrentVersion,
    DocumentDetail,
    MetadataState,
    MetadataValueInfo,
    SchemaRef,
    VerificationResult,
    VersionInfo,
)
from archivum.api.problems import ApiProblem
from archivum.api.routers.folders import EntryPatch, apply_patch
from archivum.domain import EntryNotFound

router = APIRouter(prefix="/documents", tags=["documents"])

_CHUNK = 256 * 1024


class SchemaAssign(BaseModel):
    schema_id: uuid.UUID


class ValueWrite(BaseModel):
    value: Any
    origin: str = "manual"
    source: str | None = None
    confidence: str | float | None = None


class RestoreBody(BaseModel):
    change_note: str | None = None


def fetch_document(repo, document_id: uuid.UUID) -> dict:
    try:
        info = repo.get_entry(document_id)
    except EntryNotFound:
        raise ApiProblem(404, "document_not_found", f"no document {document_id}") from None
    if info["entry_type"] != "document":
        raise ApiProblem(404, "document_not_found", f"no document {document_id}")
    return info


def _detail(repo, msvc, document_id: uuid.UUID, response: Response) -> DocumentDetail:
    info = fetch_document(repo, document_id)
    meta = msvc.get_metadata(document_id)
    response.headers["ETag"] = f'"{info["revision"]}"'
    return DocumentDetail(
        id=info["id"],
        title=info["title"],
        parent_id=info["parent_id"],
        revision=info["revision"],
        created_at=info["created_at"],
        created_by=info["created_by"],
        current_version=CurrentVersion(**info),
        schema_=SchemaRef(**meta["schema"]) if meta["schema"] else None,
        metadata_complete=meta["complete"],
    )


def _prepare_upload(request: Request, upload: UploadFile) -> tuple:
    stream = upload.file
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    if size == 0:
        raise ApiProblem(422, "empty_file", "uploaded file is empty")
    if size > request.app.state.max_upload_bytes:
        raise ApiProblem(
            413,
            "upload_too_large",
            f"upload exceeds the {request.app.state.max_upload_bytes} byte limit",
        )
    # The filename never influences storage (content-addressed); sanitize it
    # to a bare basename anyway before it becomes a title/original_filename.
    raw_name = (upload.filename or "upload").replace("\\", "/")
    safe_name = os.path.basename(raw_name) or "upload"
    mime = upload.content_type or "application/octet-stream"
    return stream, mime, safe_name


def _metadata_state(msvc, document_id: uuid.UUID) -> MetadataState:
    meta = msvc.get_metadata(document_id)
    values = []
    for v in meta["values"]:
        value = v["value"]
        if isinstance(value, Decimal):
            value = str(value)
        payload = {
            **v,
            "value": value,
            "confidence": str(v["confidence"]) if v["confidence"] is not None else None,
            "verified": v["verified_at"] is not None,
        }
        values.append(MetadataValueInfo(**payload))
    return MetadataState(
        schema_=SchemaRef(**meta["schema"]) if meta["schema"] else None,
        values=values,
        missing_required=meta["missing_required"],
        complete=meta["complete"],
    )


def _stream(handle, chunk_size: int = _CHUNK):
    try:
        while chunk := handle.read(chunk_size):
            yield chunk
    finally:
        handle.close()


def _download(repo, document_id: uuid.UUID, version_number: int | None) -> StreamingResponse:
    fetch_document(repo, document_id)
    content = repo.open_content(document_id, version_number)
    ascii_name = content["filename"].encode("ascii", "ignore").decode() or "download"
    return StreamingResponse(
        _stream(content["stream"]),
        media_type=content["mime_type"],
        headers={
            "Content-Length": str(content["size_bytes"]),
            "ETag": f'"{content["sha256"].hex()}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": (
                f'attachment; filename="{ascii_name}"; '
                f"filename*=UTF-8''{quote(content['filename'])}"
            ),
        },
    )


# ── repository ────────────────────────────────────────────────────────────


@router.post("", status_code=201, summary="Ingest a new document (multipart)")
def create_document(
    request: Request,
    response: Response,
    file: UploadFile,
    parent_id: Annotated[uuid.UUID, Form()],
    title: Annotated[str | None, Form()] = None,
    repo=Depends(get_repo),
    msvc=Depends(get_meta),
    actor=Depends(get_actor),
) -> DocumentDetail:
    stream, mime, safe_name = _prepare_upload(request, file)
    document_id = repo.ingest_document(
        actor, parent_id, stream, title or safe_name,
        mime_type=mime, original_filename=safe_name,
    )
    return _detail(repo, msvc, document_id, response)


@router.get("/{document_id}", summary="Get a document")
def get_document(
    document_id: uuid.UUID,
    response: Response,
    repo=Depends(get_repo),
    msvc=Depends(get_meta),
) -> DocumentDetail:
    return _detail(repo, msvc, document_id, response)


@router.patch("/{document_id}", summary="Rename or move a document (one change per request)")
def patch_document(
    document_id: uuid.UUID,
    body: EntryPatch,
    response: Response,
    repo=Depends(get_repo),
    msvc=Depends(get_meta),
    actor=Depends(get_actor),
    expected_revision=Depends(require_if_match),
) -> DocumentDetail:
    fetch_document(repo, document_id)
    apply_patch(repo, actor, document_id, body, expected_revision)
    return _detail(repo, msvc, document_id, response)


@router.get("/{document_id}/content", summary="Download current content")
def download_current(document_id: uuid.UUID, repo=Depends(get_repo)):
    return _download(repo, document_id, None)


# ── versions ──────────────────────────────────────────────────────────────


@router.get("/{document_id}/versions", summary="List version history")
def list_versions(document_id: uuid.UUID, repo=Depends(get_repo)) -> list[VersionInfo]:
    fetch_document(repo, document_id)
    return [VersionInfo(**v) for v in repo.list_versions(document_id)]


@router.post("/{document_id}/versions", status_code=201, summary="Add a new version (multipart)")
def create_version(
    document_id: uuid.UUID,
    request: Request,
    response: Response,
    file: UploadFile,
    change_note: Annotated[str | None, Form()] = None,
    repo=Depends(get_repo),
    actor=Depends(get_actor),
    expected_revision=Depends(require_if_match),
) -> VersionInfo:
    fetch_document(repo, document_id)
    stream, mime, safe_name = _prepare_upload(request, file)
    result = repo.create_version(
        actor, document_id, stream,
        mime_type=mime, original_filename=safe_name, change_note=change_note,
        expected_revision=expected_revision,
    )
    response.headers["ETag"] = f'"{result["revision"]}"'
    return VersionInfo(**repo.get_version(document_id, result["version_number"]))


@router.get("/{document_id}/versions/{version_number}", summary="Get one version")
def get_version(
    document_id: uuid.UUID, version_number: int, repo=Depends(get_repo)
) -> VersionInfo:
    fetch_document(repo, document_id)
    return VersionInfo(**repo.get_version(document_id, version_number))


@router.get(
    "/{document_id}/versions/{version_number}/content",
    summary="Download historical content",
)
def download_version(
    document_id: uuid.UUID, version_number: int, repo=Depends(get_repo)
):
    return _download(repo, document_id, version_number)


@router.post(
    "/{document_id}/versions/{version_number}/restore",
    status_code=201,
    summary="Restore a historical version as a new version (ADR-0007)",
)
def restore_version(
    document_id: uuid.UUID,
    version_number: int,
    response: Response,
    body: RestoreBody | None = None,
    repo=Depends(get_repo),
    actor=Depends(get_actor),
    expected_revision=Depends(require_if_match),
) -> VersionInfo:
    fetch_document(repo, document_id)
    result = repo.restore_version(
        actor, document_id, version_number,
        change_note=body.change_note if body else None,
        expected_revision=expected_revision,
    )
    response.headers["ETag"] = f'"{result["revision"]}"'
    return VersionInfo(**repo.get_version(document_id, result["version_number"]))


@router.get("/{document_id}/verification", summary="Verify all versions against their hashes")
def verification(document_id: uuid.UUID, repo=Depends(get_repo)) -> VerificationResult:
    fetch_document(repo, document_id)
    results = repo.verify_versions(document_id)
    return VerificationResult(versions=results, all_ok=all(results.values()))


# ── audit ─────────────────────────────────────────────────────────────────


@router.get("/{document_id}/audit", summary="Audit trail (business events)")
def audit(
    document_id: uuid.UUID,
    repo=Depends(get_repo),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> AuditPage:
    fetch_document(repo, document_id)
    events = repo.audit_trail(document_id)
    return AuditPage(
        items=[AuditEventInfo(**e) for e in events[offset : offset + limit]],
        total=len(events),
        limit=limit,
        offset=offset,
    )


# ── metadata ──────────────────────────────────────────────────────────────


@router.get("/{document_id}/metadata", summary="Get document metadata")
def get_metadata(
    document_id: uuid.UUID, repo=Depends(get_repo), msvc=Depends(get_meta)
) -> MetadataState:
    fetch_document(repo, document_id)
    return _metadata_state(msvc, document_id)


@router.put("/{document_id}/schema", summary="Assign an active metadata schema")
def assign_schema(
    document_id: uuid.UUID,
    body: SchemaAssign,
    response: Response,
    repo=Depends(get_repo),
    msvc=Depends(get_meta),
    actor=Depends(get_actor),
    expected_revision=Depends(require_if_match),
) -> MetadataState:
    fetch_document(repo, document_id)
    revision = msvc.assign_schema(
        actor, document_id, body.schema_id, expected_revision=expected_revision
    )
    response.headers["ETag"] = f'"{revision}"'
    return _metadata_state(msvc, document_id)


@router.put("/{document_id}/metadata/{field_key}", summary="Set a metadata value")
def set_value(
    document_id: uuid.UUID,
    field_key: str,
    body: ValueWrite,
    response: Response,
    repo=Depends(get_repo),
    msvc=Depends(get_meta),
    actor=Depends(get_actor),
    expected_revision=Depends(require_if_match),
) -> MetadataState:
    fetch_document(repo, document_id)
    result = msvc.set_metadata_value(
        actor, document_id, field_key, body.value,
        origin=body.origin, source=body.source, confidence=body.confidence,
        expected_revision=expected_revision,
    )
    response.headers["ETag"] = f'"{result["revision"]}"'
    return _metadata_state(msvc, document_id)


@router.delete("/{document_id}/metadata/{field_key}", summary="Delete a metadata value")
def delete_value(
    document_id: uuid.UUID,
    field_key: str,
    response: Response,
    repo=Depends(get_repo),
    msvc=Depends(get_meta),
    actor=Depends(get_actor),
    expected_revision=Depends(require_if_match),
) -> MetadataState:
    fetch_document(repo, document_id)
    revision = msvc.delete_metadata_value(
        actor, document_id, field_key, expected_revision=expected_revision
    )
    response.headers["ETag"] = f'"{revision}"'
    return _metadata_state(msvc, document_id)


@router.post(
    "/{document_id}/metadata/{field_key}/verify",
    summary="Verify a metadata value as a human",
)
def verify_value(
    document_id: uuid.UUID,
    field_key: str,
    response: Response,
    repo=Depends(get_repo),
    msvc=Depends(get_meta),
    actor=Depends(get_actor),
    expected_revision=Depends(require_if_match),
) -> MetadataState:
    fetch_document(repo, document_id)
    revision = msvc.verify_metadata_value(
        actor, document_id, field_key, expected_revision=expected_revision
    )
    response.headers["ETag"] = f'"{revision}"'
    return _metadata_state(msvc, document_id)
