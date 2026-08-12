"""Aggregate revision invariants (ADR-0010) + metadata_fields guard trigger.

Require PostgreSQL; skip when unavailable.
"""

import io

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from archivum.db.tables import audit_events, entries
from archivum.domain import RevisionConflict
from archivum.identity import ROOT_ENTRY_ID

PDF_A = b"%PDF revision demo A"
PDF_B = b"%PDF revision demo B"


def _revision(engine, entry_id):
    with engine.connect() as conn:
        return conn.execute(
            select(entries.c.revision).where(entries.c.id == entry_id)
        ).scalar_one()


def _audit_count(engine):
    from sqlalchemy import func

    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(audit_events)).scalar_one()


def test_every_mutation_bumps_revision_exactly_once(svc, msvc, actor, clean_db):
    folder = svc.create_folder(actor, ROOT_ENTRY_ID, "Target")
    doc = svc.ingest_document(
        actor, ROOT_ENTRY_ID, io.BytesIO(PDF_A), "doc.pdf", mime_type="application/pdf"
    )
    assert _revision(clean_db, doc) == 1  # birth state

    assert svc.rename(actor, doc, "renamed.pdf") == 2
    assert svc.move(actor, doc, folder) == 3
    assert svc.create_version(
        actor, doc, io.BytesIO(PDF_B), mime_type="application/pdf"
    )["revision"] == 4
    assert svc.restore_version(actor, doc, 1)["revision"] == 5

    sid = msvc.create_schema(actor, "Rev Schema")
    msvc.add_field(actor, sid, "field_one", "text")
    msvc.publish_schema(actor, sid)
    assert _revision(clean_db, doc) == 5  # schema lifecycle does NOT touch documents

    assert msvc.assign_schema(actor, doc, sid) == 6
    assert msvc.set_metadata_value(actor, doc, "field_one", "x")["revision"] == 7
    assert msvc.verify_metadata_value(actor, doc, "field_one") == 8
    assert msvc.delete_metadata_value(actor, doc, "field_one") == 9

    assert svc.get_entry(doc)["revision"] == 9
    assert _revision(clean_db, folder) == 1  # other entries untouched


def test_folder_rename_bumps_folder_revision(svc, actor, clean_db):
    folder = svc.create_folder(actor, ROOT_ENTRY_ID, "Folder")
    assert svc.rename(actor, folder, "Renamed") == 2
    assert _revision(clean_db, folder) == 2


def test_stale_revision_writes_nothing(svc, msvc, actor, clean_db):
    doc = svc.ingest_document(
        actor, ROOT_ENTRY_ID, io.BytesIO(PDF_A), "doc.pdf", mime_type="application/pdf"
    )
    svc.rename(actor, doc, "current.pdf")  # revision 2
    audit_before = _audit_count(clean_db)

    with pytest.raises(RevisionConflict) as exc:
        svc.rename(actor, doc, "stale.pdf", expected_revision=1)
    assert exc.value.expected == 1
    assert exc.value.actual == 2
    with pytest.raises(RevisionConflict):
        svc.create_version(
            actor, doc, io.BytesIO(PDF_B), mime_type="application/pdf", expected_revision=1
        )
    with pytest.raises(RevisionConflict):
        msvc.assign_schema(actor, doc, "nope", expected_revision=1)

    info = svc.get_entry(doc)
    assert info["title"] == "current.pdf"
    assert info["revision"] == 2  # failed bumps rolled back
    assert len(svc.list_versions(doc)) == 1
    assert _audit_count(clean_db) == audit_before


def test_correct_expected_revision_succeeds(svc, actor):
    doc = svc.ingest_document(
        actor, ROOT_ENTRY_ID, io.BytesIO(PDF_A), "doc.pdf", mime_type="application/pdf"
    )
    assert svc.rename(actor, doc, "one.pdf", expected_revision=1) == 2
    assert svc.rename(actor, doc, "two.pdf", expected_revision=2) == 3


# ── metadata_fields structural guard (migration 0005 trigger) ─────────────


def test_trigger_blocks_structural_change_on_active_schema(msvc, actor, clean_db):
    sid = msvc.create_schema(actor, "Guarded")
    msvc.add_field(actor, sid, "field_one", "text")
    msvc.publish_schema(actor, sid)

    with pytest.raises(DBAPIError):
        with clean_db.begin() as conn:
            conn.execute(
                text("UPDATE metadata_fields SET field_type = 'integer' WHERE key = 'field_one'")
            )
    with pytest.raises(DBAPIError):
        with clean_db.begin() as conn:
            conn.execute(text("DELETE FROM metadata_fields WHERE key = 'field_one'"))
    with pytest.raises(DBAPIError):
        with clean_db.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO metadata_fields "
                    "(id, schema_id, key, label, field_type, position) "
                    "VALUES (gen_random_uuid(), :s, 'smuggled', 'Smuggled', 'text', 99)"
                ),
                {"s": str(sid)},
            )


def test_trigger_allows_display_changes_and_draft_edits(msvc, actor, clean_db):
    sid = msvc.create_schema(actor, "Guarded Two")
    msvc.add_field(actor, sid, "field_one", "text")
    msvc.publish_schema(actor, sid)
    # display-only change on an active schema is allowed, even via raw SQL
    with clean_db.begin() as conn:
        conn.execute(
            text("UPDATE metadata_fields SET label = 'New Label' WHERE key = 'field_one'")
        )
    # draft schemas remain fully editable through the service
    draft = msvc.create_schema(actor, "Still Draft")
    msvc.add_field(actor, draft, "field_a", "text")
    msvc.remove_field(actor, draft, "field_a")
