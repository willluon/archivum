"""V0.4 API contract invariants (ADR-0010). Require PostgreSQL; skip when
unavailable. The `api` fixture wires the real app to the test DB and store.
"""

import io
import json
import uuid

import pytest

from archivum.identity import ROOT_ENTRY_ID

PDF_A = b"%PDF api demo A - fictional municipality"
PDF_B = b"%PDF api demo B - revised"

ROOT = str(ROOT_ENTRY_ID)


@pytest.fixture
def api(clean_db, store):
    from fastapi.testclient import TestClient

    from archivum.api import create_app

    app = create_app(engine=clean_db, store=store, max_upload_bytes=1024 * 1024)
    with TestClient(app) as client:
        client.headers.update({"X-Archivum-Actor": "api-tester"})
        yield client


def _mkfolder(api, title, parent=ROOT):
    r = api.post("/api/v1/folders", json={"title": title, "parent_id": parent})
    assert r.status_code == 201, r.text
    return r.json()["id"], r.headers["etag"]


def _ingest(api, parent=ROOT, name="permit.pdf", data=PDF_A):
    r = api.post(
        "/api/v1/documents",
        files={"file": (name, io.BytesIO(data), "application/pdf")},
        data={"parent_id": parent},
    )
    assert r.status_code == 201, r.text
    return r.json(), r.headers["etag"]


def _schema(api, name="Building Permit"):
    r = api.post("/api/v1/schemas", json={"name": name})
    sid = r.json()["id"]
    api.post(
        f"/api/v1/schemas/{sid}/fields",
        json={"key": "permit_number", "field_type": "text", "required": True},
    )
    api.post(
        f"/api/v1/schemas/{sid}/fields",
        json={"key": "property_address", "field_type": "text"},
    )
    api.post(
        f"/api/v1/schemas/{sid}/fields",
        json={"key": "estimated_cost", "field_type": "decimal"},
    )
    assert api.post(f"/api/v1/schemas/{sid}/publish").status_code == 200
    return sid


def _audit_total(api, doc_id):
    return api.get(f"/api/v1/documents/{doc_id}/audit").json()["total"]


# ── identity and DTO shape ────────────────────────────────────────────────


def test_ingest_and_get_document(api):
    doc, etag = _ingest(api)
    assert etag == '"1"'
    assert doc["current_version"]["version_number"] == 1
    assert doc["current_version"]["sha256"]
    assert doc["metadata_complete"] is True
    got = api.get(f"/api/v1/documents/{doc['id']}")
    assert got.headers["etag"] == '"1"'
    assert got.json()["id"] == doc["id"]


def test_rename_move_preserve_identity_over_http(api):
    folder_id, _ = _mkfolder(api, "Target")
    doc, etag = _ingest(api)
    r = api.patch(
        f"/api/v1/documents/{doc['id']}",
        json={"title": "BP-2026-1842.pdf"},
        headers={"If-Match": etag},
    )
    assert r.status_code == 200
    assert r.json()["id"] == doc["id"]
    assert r.headers["etag"] == '"2"'
    r = api.patch(
        f"/api/v1/documents/{doc['id']}",
        json={"parent_id": folder_id},
        headers={"If-Match": '"2"'},
    )
    assert r.status_code == 200
    assert r.json()["id"] == doc["id"]
    assert r.json()["parent_id"] == folder_id


def test_no_internals_leak_anywhere(api, store):
    doc, _ = _ingest(api)
    sid = _schema(api)
    api.put(
        f"/api/v1/documents/{doc['id']}/schema",
        json={"schema_id": sid},
        headers={"If-Match": '"1"'},
    )
    responses = [
        api.get(f"/api/v1/documents/{doc['id']}").json(),
        api.get(f"/api/v1/documents/{doc['id']}/versions").json(),
        api.get(f"/api/v1/documents/{doc['id']}/metadata").json(),
        api.get(f"/api/v1/documents/{doc['id']}/audit").json(),
        api.get(f"/api/v1/schemas/{sid}").json(),
        api.get(f"/api/v1/folders/{ROOT}/children").json(),
    ]
    blob = json.dumps(responses)
    assert "storage_key" not in blob
    assert str(store.root).replace("\\", "/") not in blob.replace("\\", "/")


def test_wrong_type_is_404(api):
    folder_id, _ = _mkfolder(api, "JustAFolder")
    doc, _ = _ingest(api)
    r = api.get(f"/api/v1/documents/{folder_id}")
    assert r.status_code == 404
    assert r.json()["code"] == "document_not_found"
    r = api.get(f"/api/v1/folders/{doc['id']}")
    assert r.status_code == 404
    assert r.json()["code"] == "folder_not_found"


# ── preconditions ─────────────────────────────────────────────────────────


def test_missing_if_match_is_428(api):
    doc, _ = _ingest(api)
    r = api.patch(f"/api/v1/documents/{doc['id']}", json={"title": "x.pdf"})
    assert r.status_code == 428
    assert r.json()["code"] == "precondition_required"
    assert r.headers["content-type"].startswith("application/problem+json")


def test_stale_if_match_is_412_with_zero_mutation(api):
    doc, etag = _ingest(api)
    api.patch(
        f"/api/v1/documents/{doc['id']}", json={"title": "current.pdf"},
        headers={"If-Match": etag},
    )
    audit_before = _audit_total(api, doc["id"])
    r = api.patch(
        f"/api/v1/documents/{doc['id']}", json={"title": "stale.pdf"},
        headers={"If-Match": etag},  # stale: revision is now 2
    )
    assert r.status_code == 412
    assert r.json()["code"] == "revision_conflict"
    info = api.get(f"/api/v1/documents/{doc['id']}").json()
    assert info["title"] == "current.pdf"
    assert _audit_total(api, doc["id"]) == audit_before


def test_stale_version_create_is_412(api):
    doc, _ = _ingest(api)
    r = api.post(
        f"/api/v1/documents/{doc['id']}/versions",
        files={"file": ("v2.pdf", io.BytesIO(PDF_B), "application/pdf")},
        headers={"If-Match": '"999"'},
    )
    assert r.status_code == 412
    assert len(api.get(f"/api/v1/documents/{doc['id']}/versions").json()) == 1


def test_alice_bob_metadata_concurrency(api):
    doc, _ = _ingest(api)
    sid = _schema(api)
    api.put(
        f"/api/v1/documents/{doc['id']}/schema", json={"schema_id": sid},
        headers={"If-Match": '"1"'},
    )
    shared_etag = api.get(f"/api/v1/documents/{doc['id']}").headers["etag"]
    alice = api.put(
        f"/api/v1/documents/{doc['id']}/metadata/property_address",
        json={"value": "10 Example Street"},
        headers={"If-Match": shared_etag},
    )
    assert alice.status_code == 200
    bob = api.put(
        f"/api/v1/documents/{doc['id']}/metadata/estimated_cost",
        json={"value": "25000.00"},
        headers={"If-Match": shared_etag},  # stale — different field, still rejected
    )
    assert bob.status_code == 412
    meta = api.get(f"/api/v1/documents/{doc['id']}/metadata").json()
    assert [v["key"] for v in meta["values"]] == ["property_address"]


# ── binary content ────────────────────────────────────────────────────────


def test_content_roundtrip_and_headers(api):
    doc, _ = _ingest(api)
    r = api.get(f"/api/v1/documents/{doc['id']}/content")
    assert r.status_code == 200
    assert r.content == PDF_A
    assert r.headers["etag"] == f'"{doc["current_version"]["sha256"]}"'
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in r.headers["content-disposition"]


def test_historical_content_and_restore_over_http(api):
    doc, etag = _ingest(api)
    api.post(
        f"/api/v1/documents/{doc['id']}/versions",
        files={"file": ("v2.pdf", io.BytesIO(PDF_B), "application/pdf")},
        headers={"If-Match": etag},
    )
    assert api.get(f"/api/v1/documents/{doc['id']}/versions/1/content").content == PDF_A
    assert api.get(f"/api/v1/documents/{doc['id']}/content").content == PDF_B
    r = api.post(
        f"/api/v1/documents/{doc['id']}/versions/1/restore",
        headers={"If-Match": '"2"'},
    )
    assert r.status_code == 201
    assert r.json()["version_number"] == 3  # restore appends, never rewinds
    assert api.get(f"/api/v1/documents/{doc['id']}/content").content == PDF_A
    numbers = [v["version_number"] for v in api.get(
        f"/api/v1/documents/{doc['id']}/versions").json()]
    assert numbers == [1, 2, 3]
    verify = api.get(f"/api/v1/documents/{doc['id']}/verification").json()
    assert verify["all_ok"] is True


def test_traversal_filename_neutralized(api):
    doc, _ = _ingest(api, name="../../../etc/evil.pdf")
    assert doc["title"] == "evil.pdf"
    assert api.get(f"/api/v1/documents/{doc['id']}/content").content == PDF_A


def test_empty_file_rejected(api):
    r = api.post(
        "/api/v1/documents",
        files={"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")},
        data={"parent_id": ROOT},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "empty_file"


def test_oversized_upload_rejected(api):
    r = api.post(
        "/api/v1/documents",
        files={"file": ("big.pdf", io.BytesIO(b"x" * (1024 * 1024 + 1)), "application/pdf")},
        data={"parent_id": ROOT},
    )
    assert r.status_code == 413
    assert r.json()["code"] == "upload_too_large"


# ── metadata over HTTP ────────────────────────────────────────────────────


def test_metadata_flow_provenance_survives_verification(api):
    doc, _ = _ingest(api)
    sid = _schema(api)
    api.put(
        f"/api/v1/documents/{doc['id']}/schema", json={"schema_id": sid},
        headers={"If-Match": '"1"'},
    )
    r = api.put(
        f"/api/v1/documents/{doc['id']}/metadata/property_address",
        json={"value": "10 Example Street", "origin": "extracted",
              "source": "demo/extractor", "confidence": "0.91"},
        headers={"If-Match": '"2"'},
    )
    assert r.status_code == 200
    r = api.post(
        f"/api/v1/documents/{doc['id']}/metadata/property_address/verify",
        headers={"If-Match": r.headers["etag"]},
    )
    value = next(v for v in r.json()["values"] if v["key"] == "property_address")
    assert value["origin"] == "extracted"
    assert value["confidence"] == "0.91"
    assert value["verified"] is True
    assert r.json()["missing_required"] == ["permit_number"]
    assert r.json()["complete"] is False


def test_invalid_metadata_value_via_http(api):
    doc, _ = _ingest(api)
    sid = _schema(api)
    api.put(
        f"/api/v1/documents/{doc['id']}/schema", json={"schema_id": sid},
        headers={"If-Match": '"1"'},
    )
    audit_before = _audit_total(api, doc["id"])
    r = api.put(
        f"/api/v1/documents/{doc['id']}/metadata/estimated_cost",
        json={"value": "banana"},
        headers={"If-Match": '"2"'},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_metadata_value"
    assert _audit_total(api, doc["id"]) == audit_before
    assert api.get(f"/api/v1/documents/{doc['id']}/metadata").json()["values"] == []
    # revision bump rolled back with the failed transaction
    assert api.get(f"/api/v1/documents/{doc['id']}").headers["etag"] == '"2"'


def test_schema_lifecycle_via_http(api):
    sid = _schema(api, name="Lifecycle")
    r = api.post(
        f"/api/v1/schemas/{sid}/fields", json={"key": "late", "field_type": "text"}
    )
    assert r.status_code == 409
    assert r.json()["code"] == "schema_state"
    assert api.post(f"/api/v1/schemas/{sid}/retire").json()["state"] == "retired"
    listing = api.get("/api/v1/schemas").json()
    assert any(s["id"] == sid and s["state"] == "retired" for s in listing)


# ── misc contract ─────────────────────────────────────────────────────────


def test_error_mapping_samples(api):
    doc, etag = _ingest(api)
    _ingest(api, name="second.pdf", data=PDF_B)
    cases = [
        (api.get(f"/api/v1/documents/{uuid.uuid4()}"), 404, "document_not_found"),
        (api.get(f"/api/v1/documents/{doc['id']}/versions/99"), 404, "version_not_found"),
        (
            api.patch(
                f"/api/v1/documents/{doc['id']}", json={"title": "second.pdf"},
                headers={"If-Match": etag},
            ),
            409,
            "title_conflict",
        ),
        (
            api.patch(
                f"/api/v1/documents/{doc['id']}",
                json={"title": "x.pdf", "parent_id": ROOT},
                headers={"If-Match": etag},
            ),
            422,
            "patch_single_change",
        ),
        (api.get("/api/v1/documents/not-a-uuid"), 422, "validation_error"),
    ]
    for response, status, code in cases:
        assert response.status_code == status, response.text
        assert response.json()["code"] == code
        assert response.headers["content-type"].startswith("application/problem+json")


def test_actor_required_and_recorded(api):
    doc, etag = _ingest(api)
    r = api.patch(
        f"/api/v1/documents/{doc['id']}", json={"title": "x.pdf"},
        headers={"If-Match": etag, "X-Archivum-Actor": ""},
    )
    assert r.status_code == 400
    assert r.json()["code"] == "actor_required"
    events = api.get(f"/api/v1/documents/{doc['id']}/audit").json()["items"]
    actors = {e["actor_id"] for e in events}
    assert len(actors) == 1  # every event attributed to the api-tester principal


def test_children_pagination(api):
    folder_id, _ = _mkfolder(api, "Paged")
    for i in range(5):
        _mkfolder(api, f"sub-{i}", parent=folder_id)
    page = api.get(
        f"/api/v1/folders/{folder_id}/children", params={"limit": 2, "offset": 2}
    ).json()
    assert page["total"] == 5
    assert [c["title"] for c in page["items"]] == ["sub-2", "sub-3"]


def test_resolve_endpoint(api):
    folder_id, _ = _mkfolder(api, "Building")
    r = api.get("/api/v1/resolve", params={"path": "/Building"})
    assert r.json() == {"id": folder_id, "type": "folder"}
    assert api.get("/api/v1/resolve", params={"path": "/Nope"}).status_code == 404


def test_api_layer_never_imports_tables():
    import pathlib

    import archivum.api

    api_dir = pathlib.Path(archivum.api.__file__).parent
    offenders = []
    for source in api_dir.rglob("*.py"):
        text = source.read_text(encoding="utf-8")
        if "db.tables import" in text or "from archivum.db import tables" in text:
            # schemas router may import FIELD_TYPES constant only
            if source.name == "schemas.py" and "FIELD_TYPES" in text:
                imported = [
                    line for line in text.splitlines() if "db.tables import" in line
                ]
                if all("FIELD_TYPES" in line and "Table" not in line for line in imported):
                    continue
            offenders.append(source.name)
    assert offenders == [], f"API modules import DB tables: {offenders}"
