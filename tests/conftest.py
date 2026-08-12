import pytest
from sqlalchemy import text

from archivum.content import LocalFilesystemContentStore
from archivum.db import create_db_engine
from archivum.identity import ensure_user_principal
from archivum.repository import RepositoryService


@pytest.fixture(scope="session")
def engine():
    from archivum.db import database_url

    url = database_url()
    # Fail the availability probe fast on machines without Postgres
    url += ("&" if "?" in url else "?") + "connect_timeout=3"
    eng = create_db_engine(url)
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1 FROM entries LIMIT 1"))
    except Exception:
        pytest.skip(
            "PostgreSQL with archivum schema not available "
            "(docker compose up -d && alembic upgrade head)"
        )
    return eng


@pytest.fixture
def clean_db(engine):
    """Reset repository state, preserving the migration-seeded system
    principal and root folder."""
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM audit_events"))
        conn.execute(text("DELETE FROM metadata_values"))
        conn.execute(text("UPDATE documents SET current_version_id = NULL"))
        conn.execute(text("DELETE FROM document_versions"))
        conn.execute(text("DELETE FROM documents"))
        conn.execute(text("DELETE FROM entries WHERE parent_id IS NOT NULL"))
        conn.execute(text("DELETE FROM metadata_fields"))
        conn.execute(text("DELETE FROM metadata_schemas"))
        conn.execute(text("DELETE FROM blobs"))
        conn.execute(text("DELETE FROM principals WHERE principal_type <> 'system'"))
    return engine


@pytest.fixture
def store(tmp_path):
    return LocalFilesystemContentStore(tmp_path / "blobstore")


@pytest.fixture
def svc(clean_db, store):
    return RepositoryService(clean_db, store)


@pytest.fixture
def actor(clean_db):
    return ensure_user_principal(clean_db, "test-user")
