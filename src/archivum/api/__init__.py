"""archivum HTTP API — the permanent external contract (ADR-0010).

FastAPI is an adapter: routers speak DTOs and call the service layer;
they never touch tables or the content store directly.
"""

import os
from pathlib import Path

from archivum import __version__
from archivum.content import LocalFilesystemContentStore
from archivum.db import create_db_engine
from archivum.metadata import MetadataService
from archivum.repository import RepositoryService


def create_app(engine=None, store=None, max_upload_bytes: int | None = None):
    from fastapi import FastAPI

    from archivum.api.problems import install_handlers
    from archivum.api.routers import documents, folders, misc, schemas

    engine = engine or create_db_engine()
    store = store or LocalFilesystemContentStore(
        Path(os.environ.get("ARCHIVUM_STORE_ROOT", "blobs"))
    )
    app = FastAPI(
        title="archivum",
        version=__version__,
        description=(
            "Portfolio-scale ECM repository API. Mutations of existing resources "
            "require If-Match with the resource ETag (revision). The "
            "X-Archivum-Actor header is development actor attribution, NOT "
            "authentication."
        ),
    )
    app.state.engine = engine
    app.state.repository = RepositoryService(engine, store)
    app.state.metadata_service = MetadataService(engine)
    app.state.max_upload_bytes = (
        max_upload_bytes
        if max_upload_bytes is not None
        else int(os.environ.get("ARCHIVUM_MAX_UPLOAD_MB", "100")) * 1024 * 1024
    )
    install_handlers(app)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    app.include_router(misc.router, prefix="/api/v1")
    app.include_router(folders.router, prefix="/api/v1")
    app.include_router(documents.router, prefix="/api/v1")
    app.include_router(schemas.router, prefix="/api/v1")
    return app
