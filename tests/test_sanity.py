"""V0 sanity checks: the package imports and DB configuration resolves.

Real invariant tests (identity survives rename/move, blob write ordering,
audit append-only) arrive with the V0.1 repository kernel.
"""

import archivum
from archivum.db import DEFAULT_DATABASE_URL, database_url


def test_package_imports():
    assert archivum.__version__


def test_database_url_default(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert database_url() == DEFAULT_DATABASE_URL


def test_database_url_env_override(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:y@host:5432/other")
    assert database_url().endswith("/other")
