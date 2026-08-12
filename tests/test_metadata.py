"""V0.3 metadata invariants (ADR-0008 / ADR-0009).

Require PostgreSQL with the archivum schema; skip when unavailable.
"""

import io
import json
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from archivum.db.tables import audit_events, metadata_values
from archivum.domain import (
    DuplicateSchemaName,
    FieldNotFound,
    InvalidMetadataValue,
    MetadataNotAssigned,
    SchemaAssignmentError,
    SchemaStateError,
)
from archivum.identity import ROOT_ENTRY_ID, SYSTEM_PRINCIPAL_ID

PDF = b"%PDF synthetic metadata demo - fictional municipality"


def _count(engine, table):
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


def _doc(svc, actor, title="permit.pdf"):
    return svc.ingest_document(
        actor, ROOT_ENTRY_ID, io.BytesIO(PDF), title, mime_type="application/pdf"
    )


def _permit_schema(msvc, actor):
    sid = msvc.create_schema(actor, "Building Permit")
    msvc.add_field(actor, sid, "permit_number", "text", required=True)
    msvc.add_field(actor, sid, "property_address", "text")
    msvc.add_field(actor, sid, "issue_date", "date")
    msvc.add_field(actor, sid, "estimated_cost", "decimal")
    msvc.publish_schema(actor, sid)
    return sid


def _invoice_schema(msvc, actor):
    sid = msvc.create_schema(actor, "Invoice")
    msvc.add_field(actor, sid, "invoice_number", "text", required=True)
    msvc.add_field(actor, sid, "vendor", "text")
    msvc.add_field(actor, sid, "amount", "decimal")
    msvc.add_field(actor, sid, "due_date", "date")
    msvc.publish_schema(actor, sid)
    return sid


def _value(meta, key):
    for v in meta["values"]:
        if v["key"] == key:
            return v
    return None


# ── generic engine ────────────────────────────────────────────────────────


def test_two_generic_schemas_coexist(svc, msvc, actor):
    permit = _permit_schema(msvc, actor)
    invoice = _invoice_schema(msvc, actor)
    doc_a, doc_b = _doc(svc, actor, "a.pdf"), _doc(svc, actor, "b.pdf")
    msvc.assign_schema(actor, doc_a, permit)
    msvc.assign_schema(actor, doc_b, invoice)
    msvc.set_metadata_value(actor, doc_a, "permit_number", "DEMO-2026-001")
    msvc.set_metadata_value(actor, doc_a, "estimated_cost", "25000.00")
    msvc.set_metadata_value(actor, doc_b, "invoice_number", "INV-0042")
    msvc.set_metadata_value(actor, doc_b, "amount", "199.99")
    assert _value(msvc.get_metadata(doc_a), "estimated_cost")["value"] == Decimal("25000.00")
    assert _value(msvc.get_metadata(doc_b), "amount")["value"] == Decimal("199.99")


def test_relabel_preserves_field_identity(msvc, actor):
    sid = _permit_schema(msvc, actor)
    before = {f["key"]: f["id"] for f in msvc.get_schema(sid)["fields"]}
    msvc.relabel_field(actor, sid, "property_address", "Site Address")
    after = {f["key"]: (f["id"], f["label"]) for f in msvc.get_schema(sid)["fields"]}
    assert after["property_address"][0] == before["property_address"]
    assert after["property_address"][1] == "Site Address"


def test_duplicate_live_schema_name_rejected(msvc, actor):
    _permit_schema(msvc, actor)
    with pytest.raises(DuplicateSchemaName):
        msvc.create_schema(actor, "building permit")  # case-insensitive


# ── lifecycle (ADR-0009) ─────────────────────────────────────────────────


def test_draft_not_assignable(svc, msvc, actor):
    sid = msvc.create_schema(actor, "Draft Schema")
    msvc.add_field(actor, sid, "field_one", "text")
    doc = _doc(svc, actor)
    with pytest.raises(SchemaStateError):
        msvc.assign_schema(actor, doc, sid)


def test_active_schema_structurally_frozen(msvc, actor):
    sid = _permit_schema(msvc, actor)
    with pytest.raises(SchemaStateError):
        msvc.add_field(actor, sid, "late_field", "text")
    with pytest.raises(SchemaStateError):
        msvc.remove_field(actor, sid, "issue_date")


def test_retired_not_assignable_but_existing_values_survive(svc, msvc, actor):
    sid = _permit_schema(msvc, actor)
    doc = _doc(svc, actor)
    msvc.assign_schema(actor, doc, sid)
    msvc.set_metadata_value(actor, doc, "permit_number", "DEMO-1")
    msvc.retire_schema(actor, sid)
    doc2 = _doc(svc, actor, "late.pdf")
    with pytest.raises(SchemaStateError):
        msvc.assign_schema(actor, doc2, sid)
    assert _value(msvc.get_metadata(doc), "permit_number")["value"] == "DEMO-1"


def test_publish_requires_a_field(msvc, actor):
    sid = msvc.create_schema(actor, "Empty Schema")
    with pytest.raises(SchemaStateError):
        msvc.publish_schema(actor, sid)


# ── assignment rules ──────────────────────────────────────────────────────


def test_schema_replacement_blocked_with_values(svc, msvc, actor, clean_db):
    permit, invoice = _permit_schema(msvc, actor), _invoice_schema(msvc, actor)
    doc = _doc(svc, actor)
    msvc.assign_schema(actor, doc, permit)
    msvc.set_metadata_value(actor, doc, "permit_number", "DEMO-1")
    with pytest.raises(SchemaAssignmentError):
        msvc.assign_schema(actor, doc, invoice)
    # DB backstop: even raw SQL cannot swap the schema while values exist
    with pytest.raises(IntegrityError):
        with clean_db.begin() as conn:
            conn.execute(
                text("UPDATE documents SET metadata_schema_id = :s WHERE entry_id = :d"),
                {"s": str(invoice), "d": str(doc)},
            )


def test_schema_replacement_allowed_without_values(svc, msvc, actor):
    permit, invoice = _permit_schema(msvc, actor), _invoice_schema(msvc, actor)
    doc = _doc(svc, actor)
    msvc.assign_schema(actor, doc, permit)
    msvc.assign_schema(actor, doc, invoice)
    assert msvc.get_metadata(doc)["schema"]["name"] == "Invoice"


def test_value_requires_assigned_schema(svc, msvc, actor):
    _permit_schema(msvc, actor)
    doc = _doc(svc, actor)
    with pytest.raises(MetadataNotAssigned):
        msvc.set_metadata_value(actor, doc, "permit_number", "DEMO-1")


# ── cross-schema protection ───────────────────────────────────────────────


def test_cross_schema_field_rejected(svc, msvc, actor, clean_db):
    permit, invoice = _permit_schema(msvc, actor), _invoice_schema(msvc, actor)
    doc = _doc(svc, actor)
    msvc.assign_schema(actor, doc, permit)
    with pytest.raises(FieldNotFound):
        msvc.set_metadata_value(actor, doc, "invoice_number", "INV-1")
    # DB backstop: raw insert of another schema's field violates a composite FK
    invoice_field = msvc.get_schema(invoice)["fields"][0]
    with pytest.raises(IntegrityError):
        with clean_db.begin() as conn:
            conn.execute(
                metadata_values.insert().values(
                    id=uuid.uuid4(),
                    document_id=doc,
                    schema_id=permit,
                    field_id=invoice_field["id"],
                    field_type="text",
                    value_text="smuggled",
                    origin="manual",
                    set_by=SYSTEM_PRINCIPAL_ID,
                )
            )


def test_db_rejects_value_column_type_mismatch(svc, msvc, actor, clean_db):
    permit = _permit_schema(msvc, actor)
    doc = _doc(svc, actor)
    msvc.assign_schema(actor, doc, permit)
    cost_field = next(
        f for f in msvc.get_schema(permit)["fields"] if f["key"] == "estimated_cost"
    )
    # value_text populated for a decimal field -> CHECK violation
    with pytest.raises(IntegrityError):
        with clean_db.begin() as conn:
            conn.execute(
                metadata_values.insert().values(
                    id=uuid.uuid4(),
                    document_id=doc,
                    schema_id=permit,
                    field_id=cost_field["id"],
                    field_type="decimal",
                    value_text="banana",
                    origin="manual",
                    set_by=SYSTEM_PRINCIPAL_ID,
                )
            )
    # lying about field_type entirely -> composite FK violation
    with pytest.raises(IntegrityError):
        with clean_db.begin() as conn:
            conn.execute(
                metadata_values.insert().values(
                    id=uuid.uuid4(),
                    document_id=doc,
                    schema_id=permit,
                    field_id=cost_field["id"],
                    field_type="text",
                    value_text="banana",
                    origin="manual",
                    set_by=SYSTEM_PRINCIPAL_ID,
                )
            )


def test_duplicate_value_per_field_impossible(svc, msvc, actor, clean_db):
    permit = _permit_schema(msvc, actor)
    doc = _doc(svc, actor)
    msvc.assign_schema(actor, doc, permit)
    msvc.set_metadata_value(actor, doc, "permit_number", "DEMO-1")
    field = next(f for f in msvc.get_schema(permit)["fields"] if f["key"] == "permit_number")
    with pytest.raises(IntegrityError):
        with clean_db.begin() as conn:
            conn.execute(
                metadata_values.insert().values(
                    id=uuid.uuid4(),
                    document_id=doc,
                    schema_id=permit,
                    field_id=field["id"],
                    field_type="text",
                    value_text="DEMO-2",
                    origin="manual",
                    set_by=SYSTEM_PRINCIPAL_ID,
                )
            )


# ── required = completeness ───────────────────────────────────────────────


def test_required_means_completeness_not_existence(svc, msvc, actor):
    permit = _permit_schema(msvc, actor)
    doc = _doc(svc, actor)
    msvc.assign_schema(actor, doc, permit)
    meta = msvc.get_metadata(doc)
    assert meta["missing_required"] == ["permit_number"]
    assert meta["complete"] is False
    msvc.set_metadata_value(actor, doc, "permit_number", "DEMO-1")
    assert msvc.get_metadata(doc)["complete"] is True
    msvc.delete_metadata_value(actor, doc, "permit_number")
    assert msvc.get_metadata(doc)["complete"] is False


# ── provenance, confidence, verification (ADR-0008) ───────────────────────


def test_extracted_provenance_retained(svc, msvc, actor):
    permit = _permit_schema(msvc, actor)
    doc = _doc(svc, actor)
    msvc.assign_schema(actor, doc, permit)
    msvc.set_metadata_value(
        actor, doc, "property_address", "10 Example Street",
        origin="extracted", source="ocr/demo", confidence="0.91",
    )
    value = _value(msvc.get_metadata(doc), "property_address")
    assert value["origin"] == "extracted"
    assert value["source"] == "ocr/demo"
    assert value["confidence"] == Decimal("0.91")
    assert value["verified_at"] is None  # machine values born unverified


def test_manual_values_born_verified(svc, msvc, actor):
    permit = _permit_schema(msvc, actor)
    doc = _doc(svc, actor)
    msvc.assign_schema(actor, doc, permit)
    msvc.set_metadata_value(actor, doc, "permit_number", "DEMO-1")
    value = _value(msvc.get_metadata(doc), "permit_number")
    assert value["origin"] == "manual"
    assert value["verified_at"] is not None
    assert value["verified_by"] == actor


def test_verification_preserves_origin_and_confidence(svc, msvc, actor):
    permit = _permit_schema(msvc, actor)
    doc = _doc(svc, actor)
    msvc.assign_schema(actor, doc, permit)
    msvc.set_metadata_value(
        actor, doc, "property_address", "10 Example Street",
        origin="extracted", source="ocr/demo", confidence="0.91",
    )
    msvc.verify_metadata_value(actor, doc, "property_address")
    value = _value(msvc.get_metadata(doc), "property_address")
    assert value["origin"] == "extracted"  # machine provenance preserved
    assert value["confidence"] == Decimal("0.91")  # how the machine felt, kept
    assert value["verified_by"] == actor
    assert value["verified_at"] is not None


def test_overwrite_resets_verification(svc, msvc, actor):
    permit = _permit_schema(msvc, actor)
    doc = _doc(svc, actor)
    msvc.assign_schema(actor, doc, permit)
    msvc.set_metadata_value(
        actor, doc, "property_address", "10 Example Street",
        origin="extracted", confidence="0.91",
    )
    msvc.verify_metadata_value(actor, doc, "property_address")
    result = msvc.set_metadata_value(
        actor, doc, "property_address", "12 Example Street",
        origin="extracted", confidence="0.85",
    )
    assert result["replaced"] is True
    value = _value(msvc.get_metadata(doc), "property_address")
    assert value["verified_at"] is None
    assert value["confidence"] == Decimal("0.85")


def test_confidence_range_enforced(svc, msvc, actor, clean_db):
    permit = _permit_schema(msvc, actor)
    doc = _doc(svc, actor)
    msvc.assign_schema(actor, doc, permit)
    with pytest.raises(InvalidMetadataValue):
        msvc.set_metadata_value(
            actor, doc, "property_address", "x", origin="extracted", confidence="1.5"
        )
    field = next(
        f for f in msvc.get_schema(permit)["fields"] if f["key"] == "property_address"
    )
    with pytest.raises(IntegrityError):
        with clean_db.begin() as conn:
            conn.execute(
                metadata_values.insert().values(
                    id=uuid.uuid4(),
                    document_id=doc,
                    schema_id=permit,
                    field_id=field["id"],
                    field_type="text",
                    value_text="x",
                    origin="extracted",
                    confidence=Decimal("1.5"),
                    set_by=SYSTEM_PRINCIPAL_ID,
                )
            )


# ── transactions and audit ────────────────────────────────────────────────


def test_invalid_value_no_row_no_audit(svc, msvc, actor, clean_db):
    permit = _permit_schema(msvc, actor)
    doc = _doc(svc, actor)
    msvc.assign_schema(actor, doc, permit)
    audit_before = _count(clean_db, audit_events)
    with pytest.raises(InvalidMetadataValue):
        msvc.set_metadata_value(actor, doc, "estimated_cost", "banana")
    assert _count(clean_db, metadata_values) == 0
    assert _count(clean_db, audit_events) == audit_before


def test_audit_failure_rolls_back_value(svc, msvc, actor, clean_db, monkeypatch):
    permit = _permit_schema(msvc, actor)
    doc = _doc(svc, actor)
    msvc.assign_schema(actor, doc, permit)

    def broken_audit(*args, **kwargs):
        raise RuntimeError("simulated audit failure")

    monkeypatch.setattr("archivum.metadata.record_event", broken_audit)
    with pytest.raises(RuntimeError):
        msvc.set_metadata_value(actor, doc, "permit_number", "DEMO-1")
    monkeypatch.undo()
    assert _count(clean_db, metadata_values) == 0


def test_audit_details_never_contain_values(svc, msvc, actor, clean_db):
    permit = _permit_schema(msvc, actor)
    doc = _doc(svc, actor)
    msvc.assign_schema(actor, doc, permit)
    secret = "SECRET-VALUE-10-Example-Street"
    msvc.set_metadata_value(actor, doc, "property_address", secret, origin="extracted")
    msvc.verify_metadata_value(actor, doc, "property_address")
    msvc.set_metadata_value(actor, doc, "property_address", secret + "-2")
    msvc.delete_metadata_value(actor, doc, "property_address")
    with clean_db.connect() as conn:
        rows = conn.execute(select(audit_events.c.action, audit_events.c.details)).all()
    assert any(r.action == "METADATA_VALUE_SET" and r.details["replaced"] for r in rows)
    for row in rows:
        assert secret not in json.dumps(row.details)


def test_metadata_actions_audited_in_order(svc, msvc, actor):
    permit = _permit_schema(msvc, actor)
    doc = _doc(svc, actor)
    msvc.assign_schema(actor, doc, permit)
    msvc.set_metadata_value(actor, doc, "permit_number", "DEMO-1")
    msvc.verify_metadata_value(actor, doc, "permit_number")
    msvc.delete_metadata_value(actor, doc, "permit_number")
    with msvc.engine.connect() as conn:
        doc_actions = [
            r.action
            for r in conn.execute(
                select(audit_events.c.action)
                .where(audit_events.c.target_id == doc)
                .order_by(audit_events.c.id)
            ).all()
        ]
    assert doc_actions == [
        "DOCUMENT_CREATED",
        "METADATA_SCHEMA_ASSIGNED",
        "METADATA_VALUE_SET",
        "METADATA_VALUE_VERIFIED",
        "METADATA_VALUE_DELETED",
    ]
