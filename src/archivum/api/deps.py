"""FastAPI dependencies: services from app state, the development actor, and
If-Match parsing. `get_actor` is the single seam the authorization milestone
replaces with real authenticated principal resolution.
"""

import re
import uuid

from fastapi import Header, Request

from archivum.api.problems import ApiProblem
from archivum.identity import ensure_user_principal
from archivum.metadata import MetadataService
from archivum.repository import RepositoryService


def get_repo(request: Request) -> RepositoryService:
    return request.app.state.repository


def get_meta(request: Request) -> MetadataService:
    return request.app.state.metadata_service


def get_actor(
    request: Request, x_archivum_actor: str | None = Header(default=None)
) -> uuid.UUID:
    """Development-only actor attribution — explicitly NOT authentication
    (ADR-0010). Required on mutations so audit is never unattributed."""
    if not x_archivum_actor or not x_archivum_actor.strip():
        raise ApiProblem(
            400,
            "actor_required",
            "mutations require the X-Archivum-Actor header "
            "(development actor attribution — not authentication)",
        )
    return ensure_user_principal(request.app.state.engine, x_archivum_actor.strip())


_QUOTED = re.compile(r'(?:W/)?"(\d+)"')
_BARE = re.compile(r"\d+")


def require_if_match(if_match: str | None = Header(default=None)) -> int | None:
    """Parse the If-Match precondition. Missing -> 428; '*' -> existence only
    (None); '"N"' -> expected revision N."""
    if if_match is None:
        raise ApiProblem(
            428,
            "precondition_required",
            "this mutation requires an If-Match header carrying the resource ETag",
        )
    value = if_match.strip()
    if value in ("*", '"*"'):
        return None
    match = _QUOTED.fullmatch(value) or _BARE.fullmatch(value)
    if match is None:
        raise ApiProblem(400, "invalid_if_match", f"malformed If-Match header: {value!r}")
    return int(match.group(1) if match.re is _QUOTED else match.group(0))
