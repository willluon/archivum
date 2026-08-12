"""Repository kernel invariants (roadmap V0.1 completion criteria).

These tests require PostgreSQL with the archivum schema; the session fixture
skips them when it is unavailable (they run in CI).
"""

import io
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from archivum.content import ContentStoreError
from archivum.db.tables import audit_events, blobs, document_versions, entries
from archivum.domain import (
    CycleDetected,
    EntryNotFound,
    InvalidOperation,
    NotAFolder,
    TitleConflict,
)
from archivum.identity import ROOT_ENTRY_ID, SYSTEM_PRINCIPAL_ID
from archivum.repository import RepositoryService

PDF = b"%PDF-1.7 synthetic demo permit, fictional municipality"


def _count(engine, table):
    with engine.connect() as conn:
        return conn.execute(select(text("count(*)")).select_from(table)).scalar_one()


def _ingest(svc, actor, parent, title, data=PDF):
    return svc.ingest_document(actor, parent, io.BytesIO(data), title, mime_type="application/pdf")


# ── identity ──────────────────────────────────────────────────────────────


def test_ingest_creates_document(svc, actor):
    doc_id = _ingest(svc, actor, ROOT_ENTRY_ID, "permit.pdf")
    info = svc.get_entry(doc_id)
    assert info["entry_type"] == "document"
    assert info["title"] == "permit.pdf"
    assert info["version_number"] == 1
    assert info["size_bytes"] == len(PDF)
    assert svc.verify_document(doc_id)


def test_rename_preserves_identity_and_content(svc, actor):
    doc_id = _ingest(svc, actor, ROOT_ENTRY_ID, "permit.pdf")
    before = svc.get_entry(doc_id)
    svc.rename(actor, doc_id, "BP-2026-1842.pdf")
    after = svc.get_entry(doc_id)
    assert after["id"] == before["id"] == doc_id
    assert after["title"] == "BP-2026-1842.pdf"
    assert after["sha256"] == before["sha256"]
    assert after["version_id"] == before["version_id"]


def test_move_preserves_identity_and_content(svc, actor):
    folder = svc.create_folder(actor, ROOT_ENTRY_ID, "Permits")
    doc_id = _ingest(svc, actor, ROOT_ENTRY_ID, "permit.pdf")
    before = svc.get_entry(doc_id)
    svc.move(actor, doc_id, folder)
    after = svc.get_entry(doc_id)
    assert after["id"] == doc_id
    assert after["parent_id"] == folder
    assert after["sha256"] == before["sha256"]
    assert svc.resolve_path("/Permits/permit.pdf") == doc_id


# ── content identity vs document identity ─────────────────────────────────


def test_same_bytes_two_documents_share_one_blob(svc, actor, clean_db):
    a = _ingest(svc, actor, ROOT_ENTRY_ID, "alice.pdf")
    b = _ingest(svc, actor, ROOT_ENTRY_ID, "bob.pdf")
    assert a != b
    assert svc.get_entry(a)["sha256"] == svc.get_entry(b)["sha256"]
    assert _count(clean_db, blobs) == 1
    assert _count(clean_db, document_versions) == 2


# ── failure modes (ADR-0003 / ADR-0004) ───────────────────────────────────


def test_blob_write_failure_leaves_no_rows(clean_db, actor):
    class FailingStore:
        def put(self, source):
            raise ContentStoreError("simulated storage outage")

    svc = RepositoryService(clean_db, FailingStore())
    with pytest.raises(ContentStoreError):
        _ingest(svc, actor, ROOT_ENTRY_ID, "permit.pdf")
    assert _count(clean_db, blobs) == 0
    assert _count(clean_db, document_versions) == 0
    assert _count(clean_db, audit_events) == 0


def test_db_failure_after_blob_leaves_orphan_blob_only(svc, actor, clean_db, store):
    missing_parent = uuid.uuid4()
    with pytest.raises(EntryNotFound):
        _ingest(svc, actor, missing_parent, "permit.pdf")
    # bytes are durable (orphan, harmless), repository is untouched
    import hashlib

    from archivum.content import key_for

    assert store.exists(key_for(hashlib.sha256(PDF).digest()))
    assert _count(clean_db, blobs) == 0
    assert _count(clean_db, document_versions) == 0
    # retry against a valid parent succeeds and reuses the stored bytes
    doc_id = _ingest(svc, actor, ROOT_ENTRY_ID, "permit.pdf")
    assert svc.verify_document(doc_id)


def test_title_conflict_rolls_back_everything(svc, actor, clean_db):
    _ingest(svc, actor, ROOT_ENTRY_ID, "permit.pdf")
    entries_before = _count(clean_db, entries)
    with pytest.raises(TitleConflict):
        _ingest(svc, actor, ROOT_ENTRY_ID, "permit.pdf", data=b"different bytes")
    assert _count(clean_db, entries) == entries_before


def test_sibling_titles_case_insensitive(svc, actor):
    _ingest(svc, actor, ROOT_ENTRY_ID, "Permit.PDF")
    with pytest.raises(TitleConflict):
        _ingest(svc, actor, ROOT_ENTRY_ID, "permit.pdf")


# ── audit (ADR-0004) ──────────────────────────────────────────────────────


def test_every_mutation_writes_its_audit_event(svc, actor):
    folder = svc.create_folder(actor, ROOT_ENTRY_ID, "Permits")
    doc_id = _ingest(svc, actor, ROOT_ENTRY_ID, "permit.pdf")
    svc.rename(actor, doc_id, "BP-2026-1842.pdf")
    svc.move(actor, doc_id, folder)

    folder_trail = [e["action"] for e in svc.audit_trail(folder)]
    doc_trail = [e["action"] for e in svc.audit_trail(doc_id)]
    assert folder_trail == ["FOLDER_CREATED"]
    assert doc_trail == ["DOCUMENT_CREATED", "ENTRY_RENAMED", "ENTRY_MOVED"]

    rename_event = svc.audit_trail(doc_id)[1]
    assert rename_event["details"]["old_title"] == "permit.pdf"
    assert rename_event["details"]["new_title"] == "BP-2026-1842.pdf"
    assert rename_event["actor_id"] == actor


# ── tree shape ────────────────────────────────────────────────────────────


def test_cycles_rejected(svc, actor):
    a = svc.create_folder(actor, ROOT_ENTRY_ID, "A")
    b = svc.create_folder(actor, a, "B")
    c = svc.create_folder(actor, b, "C")
    with pytest.raises(CycleDetected):
        svc.move(actor, a, c)
    with pytest.raises(CycleDetected):
        svc.move(actor, a, a)


def test_move_under_document_rejected(svc, actor):
    doc_id = _ingest(svc, actor, ROOT_ENTRY_ID, "permit.pdf")
    folder = svc.create_folder(actor, ROOT_ENTRY_ID, "Permits")
    with pytest.raises(NotAFolder):
        svc.move(actor, folder, doc_id)


def test_root_is_immutable(svc, actor):
    with pytest.raises(InvalidOperation):
        svc.rename(actor, ROOT_ENTRY_ID, "new-root")
    folder = svc.create_folder(actor, ROOT_ENTRY_ID, "Permits")
    with pytest.raises(InvalidOperation):
        svc.move(actor, ROOT_ENTRY_ID, folder)


# ── database-level backstops (ADR-0006) ───────────────────────────────────


def test_second_root_impossible_even_via_sql(clean_db):
    with pytest.raises(IntegrityError):
        with clean_db.begin() as conn:
            conn.execute(
                entries.insert().values(
                    id=uuid.uuid4(),
                    entry_type="folder",
                    title="rogue-root",
                    parent_id=None,
                    created_by=SYSTEM_PRINCIPAL_ID,
                )
            )


def test_document_cannot_parent_children_even_via_sql(svc, actor, clean_db):
    doc_id = _ingest(svc, actor, ROOT_ENTRY_ID, "permit.pdf")
    with pytest.raises(IntegrityError):
        with clean_db.begin() as conn:
            conn.execute(
                entries.insert().values(
                    id=uuid.uuid4(),
                    entry_type="document",
                    title="child-of-a-document",
                    parent_id=doc_id,
                    created_by=SYSTEM_PRINCIPAL_ID,
                )
            )


def test_current_version_must_belong_to_its_document(svc, actor, clean_db):
    a = _ingest(svc, actor, ROOT_ENTRY_ID, "alice.pdf", data=b"bytes A")
    b = _ingest(svc, actor, ROOT_ENTRY_ID, "bob.pdf", data=b"bytes B")
    b_version = svc.get_entry(b)["version_id"]
    with pytest.raises(IntegrityError):
        with clean_db.begin() as conn:
            conn.execute(
                text("UPDATE documents SET current_version_id = :v WHERE entry_id = :d"),
                {"v": b_version, "d": a},
            )
