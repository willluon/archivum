"""V0.2 versioning invariants (roadmap V0.2 / ADR-0007).

Require PostgreSQL with the archivum schema; skip when unavailable.
"""

import io
import threading
import uuid

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from archivum.content import ContentStoreError
from archivum.db.tables import audit_events, blobs, document_versions, documents
from archivum.domain import EntryNotFound, VersionConflict, VersionNotFound
from archivum.identity import ROOT_ENTRY_ID, SYSTEM_PRINCIPAL_ID
from archivum.repository import RepositoryService

V1 = b"%PDF synthetic contract v1 - fictional municipality"
V2 = b"%PDF synthetic contract v2 - revised terms"
V3 = b"%PDF synthetic contract v3 - final terms"


def _count(engine, table):
    from sqlalchemy import func

    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


def _doc(svc, actor, title="contract.pdf", data=V1):
    return svc.ingest_document(
        actor, ROOT_ENTRY_ID, io.BytesIO(data), title, mime_type="application/pdf"
    )


def _add(svc, actor, doc_id, data, **kwargs):
    return svc.create_version(
        actor, doc_id, io.BytesIO(data), mime_type="application/pdf", **kwargs
    )


def _version_rows(engine, doc_id):
    with engine.connect() as conn:
        return conn.execute(
            select(document_versions)
            .where(document_versions.c.document_id == doc_id)
            .order_by(document_versions.c.version_number)
        ).all()


# ── identity & append-only ────────────────────────────────────────────────


def test_new_versions_preserve_document_id(svc, actor):
    doc_id = _doc(svc, actor)
    result = _add(svc, actor, doc_id, V2)
    assert result["version_number"] == 2
    info = svc.get_entry(doc_id)
    assert info["id"] == doc_id
    assert info["version_number"] == 2


def test_versions_are_append_only(svc, actor, clean_db, store):
    doc_id = _doc(svc, actor)
    _add(svc, actor, doc_id, V2)
    before_rows = _version_rows(clean_db, doc_id)[:2]
    before_bytes = [
        open(store.root / v["storage_key"], "rb").read() for v in svc.list_versions(doc_id)
    ]
    _add(svc, actor, doc_id, V3)
    after_rows = _version_rows(clean_db, doc_id)[:2]
    assert after_rows == before_rows
    after_bytes = [
        open(store.root / v["storage_key"], "rb").read()
        for v in svc.list_versions(doc_id)[:2]
    ]
    assert after_bytes == before_bytes


def test_version_numbers_monotonic_and_unique(svc, actor, clean_db):
    doc_id = _doc(svc, actor)
    _add(svc, actor, doc_id, V2)
    _add(svc, actor, doc_id, V3)
    numbers = [v["version_number"] for v in svc.list_versions(doc_id)]
    assert numbers == [1, 2, 3]
    existing = _version_rows(clean_db, doc_id)[0]
    with pytest.raises(IntegrityError):
        with clean_db.begin() as conn:
            conn.execute(
                document_versions.insert().values(
                    id=uuid.uuid4(),
                    document_id=doc_id,
                    version_number=3,  # duplicate (document_id, version_number)
                    blob_id=existing.blob_id,
                    mime_type="application/pdf",
                    created_by=SYSTEM_PRINCIPAL_ID,
                )
            )


def test_current_pointer_advances(svc, actor, clean_db):
    doc_id = _doc(svc, actor)
    result = _add(svc, actor, doc_id, V2)
    with clean_db.connect() as conn:
        current = conn.execute(
            select(documents.c.current_version_id).where(documents.c.entry_id == doc_id)
        ).scalar_one()
    assert current == result["version_id"]
    assert [v["is_current"] for v in svc.list_versions(doc_id)] == [False, True]


# ── blob identity vs version identity ─────────────────────────────────────


def test_identical_content_new_version_reuses_blob(svc, actor, clean_db):
    doc_id = _doc(svc, actor)
    _add(svc, actor, doc_id, V1)  # same bytes: a new event, not an error
    versions = svc.list_versions(doc_id)
    assert len(versions) == 2
    assert versions[0]["sha256"] == versions[1]["sha256"]
    assert _count(clean_db, blobs) == 1


def test_version_can_reference_older_blob(svc, actor, clean_db):
    doc_id = _doc(svc, actor)
    _add(svc, actor, doc_id, V2)
    _add(svc, actor, doc_id, V1)  # v3 returns to v1's bytes
    v = {x["version_number"]: x for x in svc.list_versions(doc_id)}
    assert v[3]["sha256"] == v[1]["sha256"]
    assert v[2]["sha256"] != v[1]["sha256"]
    assert _count(clean_db, blobs) == 2


# ── optimistic concurrency ────────────────────────────────────────────────


def test_expected_version_mismatch_conflicts(svc, actor, clean_db):
    doc_id = _doc(svc, actor)
    _add(svc, actor, doc_id, V2)
    audit_before = _count(clean_db, audit_events)
    with pytest.raises(VersionConflict) as exc:
        _add(svc, actor, doc_id, V3, expected_version=1)
    assert exc.value.expected == 1
    assert exc.value.actual == 2
    assert len(svc.list_versions(doc_id)) == 2
    assert svc.get_entry(doc_id)["version_number"] == 2
    assert _count(clean_db, audit_events) == audit_before


def test_concurrent_writer_blocks_on_lock_then_conflicts(svc, actor, clean_db):
    """Deterministic lock-ordering proof: hold the documents-row lock, show a
    competing create_version(expected_version=1) blocks on it, advance the
    document to v2 under the lock, commit — the writer must then conflict."""
    doc_id = _doc(svc, actor)
    outcome = {}

    def worker():
        try:
            outcome["result"] = _add(svc, actor, doc_id, V2, expected_version=1)
        except Exception as exc:  # noqa: BLE001 - recorded for assertion
            outcome["result"] = exc

    thread = threading.Thread(target=worker)
    with clean_db.connect() as conn:
        with conn.begin():
            conn.execute(
                select(documents.c.entry_id)
                .where(documents.c.entry_id == doc_id)
                .with_for_update()
            )
            thread.start()
            thread.join(1.0)
            assert thread.is_alive(), "writer should be blocked on the documents-row lock"
            v1 = conn.execute(
                select(document_versions).where(document_versions.c.document_id == doc_id)
            ).one()
            rival_id = uuid.uuid4()
            conn.execute(
                document_versions.insert().values(
                    id=rival_id,
                    document_id=doc_id,
                    version_number=2,
                    blob_id=v1.blob_id,
                    mime_type="application/pdf",
                    created_by=SYSTEM_PRINCIPAL_ID,
                )
            )
            conn.execute(
                update(documents)
                .where(documents.c.entry_id == doc_id)
                .values(current_version_id=rival_id)
            )
        # commit released the lock; the writer resumes and must conflict
    thread.join(10.0)
    assert isinstance(outcome["result"], VersionConflict)
    assert len(svc.list_versions(doc_id)) == 2


def test_racing_writers_exactly_one_succeeds(svc, actor, clean_db):
    doc_id = _doc(svc, actor)
    barrier = threading.Barrier(2)
    outcomes = []

    def attempt(data):
        barrier.wait()
        try:
            outcomes.append(("ok", _add(svc, actor, doc_id, data, expected_version=1)))
        except VersionConflict:
            outcomes.append(("conflict", None))

    threads = [threading.Thread(target=attempt, args=(d,)) for d in (V2, V3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(15.0)
    assert sorted(kind for kind, _ in outcomes) == ["conflict", "ok"]
    assert [v["version_number"] for v in svc.list_versions(doc_id)] == [1, 2]


# ── restore (ADR-0007) ────────────────────────────────────────────────────


def test_restore_creates_new_version(svc, actor, clean_db):
    doc_id = _doc(svc, actor)
    _add(svc, actor, doc_id, V2)
    _add(svc, actor, doc_id, V3)
    history_before = _version_rows(clean_db, doc_id)
    result = svc.restore_version(actor, doc_id, 1)
    assert result["version_number"] == 4
    v = {x["version_number"]: x for x in svc.list_versions(doc_id)}
    assert v[4]["sha256"] == v[1]["sha256"]
    assert v[4]["is_current"]
    assert v[4]["change_note"] == "restored from version 1"
    assert _version_rows(clean_db, doc_id)[:3] == history_before  # v1-v3 untouched
    assert svc.get_entry(doc_id)["id"] == doc_id
    last_event = svc.audit_trail(doc_id)[-1]
    assert last_event["action"] == "DOCUMENT_VERSION_RESTORED"
    assert last_event["details"]["restored_from_version_number"] == 1
    assert last_event["details"]["version_number"] == 4


def test_restore_missing_version(svc, actor):
    doc_id = _doc(svc, actor)
    with pytest.raises(VersionNotFound):
        svc.restore_version(actor, doc_id, 99)


def test_restore_respects_expected_version(svc, actor):
    doc_id = _doc(svc, actor)
    _add(svc, actor, doc_id, V2)
    with pytest.raises(VersionConflict):
        svc.restore_version(actor, doc_id, 1, expected_version=1)
    assert len(svc.list_versions(doc_id)) == 2


# ── failure modes ─────────────────────────────────────────────────────────


def test_blob_failure_creates_nothing(svc, actor, clean_db):
    doc_id = _doc(svc, actor)

    class FailingStore:
        def put(self, source):
            raise ContentStoreError("simulated storage outage")

    broken = RepositoryService(clean_db, FailingStore())
    audit_before = _count(clean_db, audit_events)
    with pytest.raises(ContentStoreError):
        broken.create_version(actor, doc_id, io.BytesIO(V2), mime_type="application/pdf")
    assert len(svc.list_versions(doc_id)) == 1
    assert _count(clean_db, audit_events) == audit_before


def test_db_failure_after_blob_orphans_only(svc, actor, clean_db, store):
    import hashlib

    from archivum.content import key_for

    with pytest.raises(EntryNotFound):
        svc.create_version(actor, uuid.uuid4(), io.BytesIO(V2), mime_type="application/pdf")
    assert store.exists(key_for(hashlib.sha256(V2).digest()))  # durable orphan
    assert _count(clean_db, document_versions) == 0
    # a valid retry reuses the stored bytes
    doc_id = _doc(svc, actor)
    _add(svc, actor, doc_id, V2)
    assert svc.verify_versions(doc_id) == {1: True, 2: True}


def test_audit_failure_rolls_back_version(svc, actor, clean_db, store, monkeypatch):
    doc_id = _doc(svc, actor)

    def broken_audit(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr("archivum.repository.record_event", broken_audit)
    with pytest.raises(RuntimeError):
        _add(svc, actor, doc_id, V2)
    monkeypatch.undo()
    assert len(svc.list_versions(doc_id)) == 1
    assert svc.get_entry(doc_id)["version_number"] == 1
    assert [e["action"] for e in svc.audit_trail(doc_id)] == ["DOCUMENT_CREATED"]


# ── integrity ─────────────────────────────────────────────────────────────


def test_all_versions_individually_verifiable(svc, actor, store):
    doc_id = _doc(svc, actor)
    _add(svc, actor, doc_id, V2)
    _add(svc, actor, doc_id, V3)
    svc.restore_version(actor, doc_id, 1)
    assert svc.verify_versions(doc_id) == {1: True, 2: True, 3: True, 4: True}
    # tamper with v2's blob on disk: exactly v2 (and only v2) fails
    v2_key = svc.get_version(doc_id, 2)["storage_key"]
    (store.root / v2_key).write_bytes(b"tampered")
    assert svc.verify_versions(doc_id) == {1: True, 2: False, 3: True, 4: True}
