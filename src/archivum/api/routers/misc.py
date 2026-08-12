from fastapi import APIRouter, Depends

from archivum.api.deps import get_repo
from archivum.api.dto import ResolveResult

router = APIRouter(tags=["repository"])


@router.get("/resolve", summary="Resolve a slash path to an entry id")
def resolve(path: str, repo=Depends(get_repo)) -> ResolveResult:
    entry_id = repo.resolve_path(path)
    info = repo.get_entry(entry_id)
    return ResolveResult(id=entry_id, type=info["entry_type"])
