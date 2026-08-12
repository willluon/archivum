"""Repository kernel services: the only write path to repository state.

Every mutating operation is one transaction that includes its audit event
(ADR-0004). Document ingestion is blob-first (ADR-0003): bytes reach the
ContentStore before the transaction that references them, so a failure can
orphan a blob (harmless, invisible) but never a document row without bytes.
"""

import uuid
from typing import BinaryIO

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError

from archivum.audit import record_event, trail
from archivum.content import ContentStore, PutResult
from archivum.db.tables import blobs, document_versions, documents, entries
from archivum.domain import (
    CycleDetected,
    EntryNotFound,
    InvalidOperation,
    NotAFolder,
    TitleConflict,
    new_id,
    validate_title,
)
from archivum.identity import ROOT_ENTRY_ID


class RepositoryService:
    def __init__(self, engine: Engine, store: ContentStore):
        self.engine = engine
        self.store = store

    # ── reads ──────────────────────────────────────────────────────────────

    def get_entry(self, entry_id: uuid.UUID) -> dict:
        with self.engine.connect() as conn:
            entry = self._active_entry(conn, entry_id)
            result = dict(entry._mapping)
            if entry.entry_type == "document":
                version = conn.execute(
                    select(
                        document_versions.c.id.label("version_id"),
                        document_versions.c.version_number,
                        document_versions.c.mime_type,
                        document_versions.c.original_filename,
                        blobs.c.sha256,
                        blobs.c.size_bytes,
                        blobs.c.storage_key,
                    )
                    .select_from(
                        documents.join(
                            document_versions,
                            documents.c.current_version_id == document_versions.c.id,
                        ).join(blobs, document_versions.c.blob_id == blobs.c.id)
                    )
                    .where(documents.c.entry_id == entry_id)
                ).one()
                result.update(dict(version._mapping))
            return result

    def list_folder(self, folder_id: uuid.UUID) -> list[dict]:
        with self.engine.connect() as conn:
            folder = self._active_entry(conn, folder_id)
            if folder.entry_type != "folder":
                raise NotAFolder(f"{folder_id} is not a folder")
            rows = conn.execute(
                select(entries.c.id, entries.c.entry_type, entries.c.title)
                .where(entries.c.parent_id == folder_id, entries.c.state == "active")
                .order_by(entries.c.entry_type.desc(), func.lower(entries.c.title))
            ).all()
            return [dict(r._mapping) for r in rows]

    def audit_trail(self, entry_id: uuid.UUID) -> list[dict]:
        with self.engine.connect() as conn:
            return trail(conn, entry_id)

    def resolve_path(self, path: str) -> uuid.UUID:
        """Walk titles from the root: '/Building/Permits' -> entry id."""
        current = ROOT_ENTRY_ID
        with self.engine.connect() as conn:
            for part in [p for p in path.split("/") if p]:
                found = conn.execute(
                    select(entries.c.id).where(
                        entries.c.parent_id == current,
                        func.lower(entries.c.title) == part.lower(),
                        entries.c.state == "active",
                    )
                ).scalar_one_or_none()
                if found is None:
                    raise EntryNotFound(f"no entry at path {path!r} (missing {part!r})")
                current = found
        return current

    def verify_document(self, entry_id: uuid.UUID) -> bool:
        info = self.get_entry(entry_id)
        if info["entry_type"] != "document":
            raise InvalidOperation("verify applies to documents")
        return self.store.verify(info["storage_key"], info["sha256"])

    # ── writes (one transaction each, audit in-transaction) ───────────────

    def create_folder(self, actor_id: uuid.UUID, parent_id: uuid.UUID, title: str) -> uuid.UUID:
        validate_title(title)
        with self.engine.begin() as conn:
            self._require_folder(conn, parent_id)
            self._require_title_free(conn, parent_id, title)
            folder_id = new_id()
            self._insert_entry(conn, folder_id, "folder", title, parent_id, actor_id)
            record_event(
                conn,
                actor_id=actor_id,
                action="FOLDER_CREATED",
                target_id=folder_id,
                details={"title": title, "parent_id": str(parent_id)},
            )
        return folder_id

    def ingest_document(
        self,
        actor_id: uuid.UUID,
        parent_id: uuid.UUID,
        source: BinaryIO,
        title: str,
        mime_type: str = "application/octet-stream",
        original_filename: str | None = None,
    ) -> uuid.UUID:
        validate_title(title)
        # Blob-first (ADR-0003): bytes are durable before any row references them.
        put = self.store.put(source)
        with self.engine.begin() as conn:
            self._require_folder(conn, parent_id)
            self._require_title_free(conn, parent_id, title)
            blob_id = self._ensure_blob(conn, put)
            document_id = new_id()
            self._insert_entry(conn, document_id, "document", title, parent_id, actor_id)
            conn.execute(documents.insert().values(entry_id=document_id))
            version_id = new_id()
            conn.execute(
                document_versions.insert().values(
                    id=version_id,
                    document_id=document_id,
                    version_number=1,
                    blob_id=blob_id,
                    mime_type=mime_type,
                    original_filename=original_filename,
                    created_by=actor_id,
                )
            )
            conn.execute(
                update(documents)
                .where(documents.c.entry_id == document_id)
                .values(current_version_id=version_id)
            )
            record_event(
                conn,
                actor_id=actor_id,
                action="DOCUMENT_CREATED",
                target_id=document_id,
                details={
                    "title": title,
                    "parent_id": str(parent_id),
                    "version_number": 1,
                    "sha256": put.sha256.hex(),
                    "size_bytes": put.size_bytes,
                },
            )
        return document_id

    def rename(self, actor_id: uuid.UUID, entry_id: uuid.UUID, new_title: str) -> None:
        validate_title(new_title)
        with self.engine.begin() as conn:
            entry = self._active_entry(conn, entry_id, for_update=True)
            if entry.parent_id is None:
                raise InvalidOperation("the root folder cannot be renamed")
            if entry.title == new_title:
                return
            self._require_title_free(conn, entry.parent_id, new_title, exclude=entry_id)
            self._try(
                conn.execute,
                update(entries)
                .where(entries.c.id == entry_id)
                .values(title=new_title, updated_at=func.now()),
            )
            record_event(
                conn,
                actor_id=actor_id,
                action="ENTRY_RENAMED",
                target_id=entry_id,
                details={"old_title": entry.title, "new_title": new_title},
            )

    def move(self, actor_id: uuid.UUID, entry_id: uuid.UUID, new_parent_id: uuid.UUID) -> None:
        with self.engine.begin() as conn:
            entry = self._active_entry(conn, entry_id, for_update=True)
            if entry.parent_id is None:
                raise InvalidOperation("the root folder cannot be moved")
            self._require_folder(conn, new_parent_id, for_update=True)
            # Cycle check: the destination must not be the entry or any of
            # its descendants — walk the destination's ancestor chain.
            cursor: uuid.UUID | None = new_parent_id
            while cursor is not None:
                if cursor == entry_id:
                    raise CycleDetected("cannot move a folder into itself or its descendants")
                cursor = conn.execute(
                    select(entries.c.parent_id).where(entries.c.id == cursor)
                ).scalar_one()
            self._require_title_free(conn, new_parent_id, entry.title, exclude=entry_id)
            self._try(
                conn.execute,
                update(entries)
                .where(entries.c.id == entry_id)
                .values(parent_id=new_parent_id, updated_at=func.now()),
            )
            record_event(
                conn,
                actor_id=actor_id,
                action="ENTRY_MOVED",
                target_id=entry_id,
                details={
                    "old_parent_id": str(entry.parent_id),
                    "new_parent_id": str(new_parent_id),
                },
            )

    # ── internals ─────────────────────────────────────────────────────────

    def _active_entry(self, conn: Connection, entry_id: uuid.UUID, for_update: bool = False):
        stmt = select(
            entries.c.id,
            entries.c.entry_type,
            entries.c.title,
            entries.c.parent_id,
            entries.c.state,
            entries.c.created_at,
            entries.c.created_by,
        ).where(entries.c.id == entry_id, entries.c.state == "active")
        if for_update:
            stmt = stmt.with_for_update()
        row = conn.execute(stmt).one_or_none()
        if row is None:
            raise EntryNotFound(str(entry_id))
        return row

    def _require_folder(self, conn: Connection, entry_id: uuid.UUID, for_update: bool = False):
        entry = self._active_entry(conn, entry_id, for_update=for_update)
        if entry.entry_type != "folder":
            raise NotAFolder(f"{entry_id} is not a folder")
        return entry

    def _require_title_free(
        self,
        conn: Connection,
        parent_id: uuid.UUID,
        title: str,
        exclude: uuid.UUID | None = None,
    ) -> None:
        stmt = select(entries.c.id).where(
            entries.c.parent_id == parent_id,
            func.lower(entries.c.title) == title.lower(),
            entries.c.state == "active",
        )
        if exclude is not None:
            stmt = stmt.where(entries.c.id != exclude)
        if conn.execute(stmt).first() is not None:
            raise TitleConflict(f"an entry titled {title!r} already exists here")

    def _insert_entry(self, conn, entry_id, entry_type, title, parent_id, actor_id) -> None:
        self._try(
            conn.execute,
            entries.insert().values(
                id=entry_id,
                entry_type=entry_type,
                title=title,
                parent_id=parent_id,
                created_by=actor_id,
            ),
        )

    def _ensure_blob(self, conn: Connection, put: PutResult) -> uuid.UUID:
        conn.execute(
            pg_insert(blobs)
            .values(
                id=new_id(),
                sha256=put.sha256,
                size_bytes=put.size_bytes,
                storage_key=put.key,
            )
            .on_conflict_do_nothing(index_elements=[blobs.c.sha256])
        )
        return conn.execute(
            select(blobs.c.id).where(blobs.c.sha256 == put.sha256)
        ).scalar_one()

    @staticmethod
    def _try(fn, stmt):
        """Map race-window unique-index violations to the domain error the
        pre-check would have raised."""
        try:
            return fn(stmt)
        except IntegrityError as exc:
            if "uq_entries_sibling_title" in str(exc.orig):
                raise TitleConflict("an entry with that title already exists here") from exc
            raise
