"""RFC 9457 problem details: every error is application/problem+json with a
stable machine `code` (ADR-0010). Domain exceptions map here; driver
exceptions and stack traces never reach a response.
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from archivum.domain import (
    CycleDetected,
    DomainError,
    DuplicateFieldKey,
    DuplicateSchemaName,
    EntryNotFound,
    FieldNotFound,
    InvalidFieldKey,
    InvalidMetadataValue,
    InvalidOperation,
    InvalidTitle,
    MetadataNotAssigned,
    NotADocument,
    NotAFolder,
    RevisionConflict,
    SchemaAssignmentError,
    SchemaNotFound,
    SchemaStateError,
    TitleConflict,
    VersionConflict,
    VersionNotFound,
)


class ApiProblem(Exception):
    """API-layer problem (preconditions, upload limits, typed 404s)."""

    def __init__(self, status: int, code: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


_DOMAIN_MAP: dict[type, tuple[int, str]] = {
    EntryNotFound: (404, "entry_not_found"),
    VersionNotFound: (404, "version_not_found"),
    SchemaNotFound: (404, "schema_not_found"),
    FieldNotFound: (404, "field_not_found"),
    NotADocument: (404, "document_not_found"),
    TitleConflict: (409, "title_conflict"),
    CycleDetected: (409, "hierarchy_cycle"),
    NotAFolder: (409, "not_a_folder"),
    InvalidOperation: (409, "invalid_operation"),
    SchemaStateError: (409, "schema_state"),
    SchemaAssignmentError: (409, "schema_assignment"),
    DuplicateSchemaName: (409, "duplicate_schema_name"),
    DuplicateFieldKey: (409, "duplicate_field_key"),
    MetadataNotAssigned: (409, "metadata_not_assigned"),
    RevisionConflict: (412, "revision_conflict"),
    VersionConflict: (412, "version_conflict"),
    InvalidTitle: (422, "invalid_title"),
    InvalidFieldKey: (422, "invalid_field_key"),
    InvalidMetadataValue: (422, "invalid_metadata_value"),
}


def problem_response(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={
            "type": f"urn:archivum:problem:{code}",
            "title": code.replace("_", " "),
            "status": status,
            "detail": detail,
            "code": code,
        },
    )


def install_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiProblem)
    async def api_problem_handler(request: Request, exc: ApiProblem):
        return problem_response(exc.status, exc.code, exc.detail)

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError):
        for klass in type(exc).__mro__:
            if klass in _DOMAIN_MAP:
                status, code = _DOMAIN_MAP[klass]
                return problem_response(status, code, str(exc))
        return problem_response(400, "domain_error", str(exc))

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return problem_response(422, "validation_error", str(exc.errors()[:3]))

    @app.exception_handler(Exception)
    async def internal_handler(request: Request, exc: Exception):
        # Deliberately generic: no exception text, no stack trace
        return problem_response(500, "internal_error", "internal server error")
