"""Database wiring: engine construction and the single MetaData registry.

All tables across every module register on `metadata` so Alembic sees one
consistent schema. The naming convention makes constraint names deterministic,
which keeps migrations reversible and diffable.
"""

import os

from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import Engine

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)

DEFAULT_DATABASE_URL = "postgresql+psycopg://archivum:archivum@localhost:5432/archivum"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def create_db_engine(url: str | None = None) -> Engine:
    return create_engine(url or database_url())
