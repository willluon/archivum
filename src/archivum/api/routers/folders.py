import uuid

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel

from archivum.api.deps import get_actor, get_repo, require_if_match
from archivum.api.dto import ChildInfo, ChildPage, FolderDetail
from archivum.api.problems import ApiProblem
from archivum.domain import EntryNotFound

router = APIRouter(prefix="/folders", tags=["folders"])


class FolderCreate(BaseModel):
    title: str
    parent_id: uuid.UUID


class EntryPatch(BaseModel):
    title: str | None = None
    parent_id: uuid.UUID | None = None


def fetch_folder(repo, folder_id: uuid.UUID) -> dict:
    try:
        info = repo.get_entry(folder_id)
    except EntryNotFound:
        raise ApiProblem(404, "folder_not_found", f"no folder {folder_id}") from None
    if info["entry_type"] != "folder":
        raise ApiProblem(404, "folder_not_found", f"no folder {folder_id}")
    return info


def apply_patch(repo, actor, entry_id, patch: EntryPatch, expected_revision) -> None:
    """Shared single-change PATCH semantics for folders and documents."""
    changes = [v for v in (patch.title, patch.parent_id) if v is not None]
    if len(changes) != 1:
        raise ApiProblem(
            422,
            "patch_single_change",
            "PATCH accepts exactly one of 'title' or 'parent_id' per request",
        )
    if patch.title is not None:
        repo.rename(actor, entry_id, patch.title, expected_revision=expected_revision)
    else:
        repo.move(actor, entry_id, patch.parent_id, expected_revision=expected_revision)


def _detail(info: dict) -> FolderDetail:
    return FolderDetail(**info)


@router.post("", status_code=201, summary="Create a folder")
def create_folder(
    body: FolderCreate,
    response: Response,
    repo=Depends(get_repo),
    actor=Depends(get_actor),
) -> FolderDetail:
    folder_id = repo.create_folder(actor, body.parent_id, body.title)
    info = fetch_folder(repo, folder_id)
    response.headers["ETag"] = f'"{info["revision"]}"'
    return _detail(info)


@router.get("/{folder_id}", summary="Get a folder")
def get_folder(
    folder_id: uuid.UUID, response: Response, repo=Depends(get_repo)
) -> FolderDetail:
    info = fetch_folder(repo, folder_id)
    response.headers["ETag"] = f'"{info["revision"]}"'
    return _detail(info)


@router.get("/{folder_id}/children", summary="List a folder's children")
def list_children(
    folder_id: uuid.UUID,
    repo=Depends(get_repo),
    type: str | None = Query(default=None, pattern="^(folder|document)$"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> ChildPage:
    fetch_folder(repo, folder_id)
    children = repo.list_folder(folder_id)
    if type is not None:
        children = [c for c in children if c["entry_type"] == type]
    page = children[offset : offset + limit]
    return ChildPage(
        items=[ChildInfo(id=c["id"], type=c["entry_type"], title=c["title"]) for c in page],
        total=len(children),
        limit=limit,
        offset=offset,
    )


@router.patch("/{folder_id}", summary="Rename or move a folder (one change per request)")
def patch_folder(
    folder_id: uuid.UUID,
    body: EntryPatch,
    response: Response,
    repo=Depends(get_repo),
    actor=Depends(get_actor),
    expected_revision=Depends(require_if_match),
) -> FolderDetail:
    fetch_folder(repo, folder_id)
    apply_patch(repo, actor, folder_id, body, expected_revision)
    info = fetch_folder(repo, folder_id)
    response.headers["ETag"] = f'"{info["revision"]}"'
    return _detail(info)
