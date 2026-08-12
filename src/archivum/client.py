"""HTTP client for the archivum API — the CLI's only path to the repository.

Every operation goes through /api/v1; mutating helpers fetch the current
ETag first and send If-Match, so the CLI exercises the concurrency contract
on every call.
"""

import os

import httpx


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.status = status
        self.code = code
        self.detail = detail


class ApiUnreachable(Exception):
    pass


class ArchivumClient:
    def __init__(self, base_url: str | None = None, actor: str | None = None):
        self.base_url = (
            base_url or os.environ.get("ARCHIVUM_API_URL", "http://127.0.0.1:8000")
        ).rstrip("/")
        headers = {"X-Archivum-Actor": actor} if actor else {}
        self.http = httpx.Client(
            base_url=self.base_url + "/api/v1", timeout=120.0, headers=headers
        )

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            response = self.http.request(method, url, **kwargs)
        except httpx.ConnectError as exc:
            raise ApiUnreachable(
                f"cannot reach the archivum API at {self.base_url} "
                "(start it with: archivum serve)"
            ) from exc
        if response.status_code >= 400:
            try:
                problem = response.json()
                raise ApiError(
                    response.status_code,
                    problem.get("code", "error"),
                    problem.get("detail") or problem.get("title", ""),
                )
            except ValueError:
                raise ApiError(response.status_code, "error", response.text[:300]) from None
        return response

    # ── reads ─────────────────────────────────────────────────────────────

    def resolve(self, path: str) -> dict:
        return self._request("GET", "/resolve", params={"path": path}).json()

    def get_document(self, document_id: str) -> tuple[dict, str]:
        r = self._request("GET", f"/documents/{document_id}")
        return r.json(), r.headers["etag"]

    def get_folder(self, folder_id: str) -> tuple[dict, str]:
        r = self._request("GET", f"/folders/{folder_id}")
        return r.json(), r.headers["etag"]

    def children(self, folder_id: str) -> list[dict]:
        return self._request(
            "GET", f"/folders/{folder_id}/children", params={"limit": 500}
        ).json()["items"]

    def versions(self, document_id: str) -> list[dict]:
        return self._request("GET", f"/documents/{document_id}/versions").json()

    def audit(self, document_id: str) -> list[dict]:
        return self._request(
            "GET", f"/documents/{document_id}/audit", params={"limit": 500}
        ).json()["items"]

    def verification(self, document_id: str) -> dict:
        return self._request("GET", f"/documents/{document_id}/verification").json()

    def metadata(self, document_id: str) -> dict:
        return self._request("GET", f"/documents/{document_id}/metadata").json()

    def schemas(self) -> list[dict]:
        return self._request("GET", "/schemas").json()

    def schema(self, schema_id: str) -> dict:
        return self._request("GET", f"/schemas/{schema_id}").json()

    def schema_by_name(self, name: str) -> dict:
        live = [
            s for s in self.schemas()
            if s["name"].lower() == name.lower() and s["state"] != "retired"
        ]
        if not live:
            raise ApiError(404, "schema_not_found", f"no live schema named {name!r}")
        return self.schema(live[0]["id"])

    # ── mutations (GET-then-mutate with If-Match) ─────────────────────────

    def _etag_for(self, entry_id: str, entry_type: str) -> str:
        if entry_type == "document":
            return self.get_document(entry_id)[1]
        return self.get_folder(entry_id)[1]

    def create_folder(self, parent_id: str, title: str) -> dict:
        return self._request(
            "POST", "/folders", json={"title": title, "parent_id": parent_id}
        ).json()

    def ingest(self, parent_id: str, file_path, title: str | None, mime: str) -> dict:
        with open(file_path, "rb") as f:
            return self._request(
                "POST",
                "/documents",
                files={"file": (os.path.basename(str(file_path)), f, mime)},
                data={"parent_id": parent_id, **({"title": title} if title else {})},
            ).json()

    def patch_entry(self, entry_id: str, entry_type: str, expect: int | None, **change) -> dict:
        etag = f'"{expect}"' if expect is not None else self._etag_for(entry_id, entry_type)
        path = f"/documents/{entry_id}" if entry_type == "document" else f"/folders/{entry_id}"
        return self._request("PATCH", path, json=change, headers={"If-Match": etag}).json()

    def add_version(
        self, document_id: str, file_path, mime: str, note: str | None, expect: int | None
    ) -> dict:
        etag = f'"{expect}"' if expect is not None else self.get_document(document_id)[1]
        with open(file_path, "rb") as f:
            return self._request(
                "POST",
                f"/documents/{document_id}/versions",
                files={"file": (os.path.basename(str(file_path)), f, mime)},
                data={"change_note": note} if note else {},
                headers={"If-Match": etag},
            ).json()

    def restore(
        self, document_id: str, version_number: int, note: str | None, expect: int | None
    ) -> dict:
        etag = f'"{expect}"' if expect is not None else self.get_document(document_id)[1]
        return self._request(
            "POST",
            f"/documents/{document_id}/versions/{version_number}/restore",
            json={"change_note": note} if note else {},
            headers={"If-Match": etag},
        ).json()

    def create_schema(self, name: str, description: str | None) -> dict:
        body = {"name": name}
        if description:
            body["description"] = description
        return self._request("POST", "/schemas", json=body).json()

    def add_field(self, schema_id: str, key: str, field_type: str, label, required) -> dict:
        return self._request(
            "POST",
            f"/schemas/{schema_id}/fields",
            json={"key": key, "field_type": field_type, "label": label, "required": required},
        ).json()

    def publish_schema(self, schema_id: str) -> dict:
        return self._request("POST", f"/schemas/{schema_id}/publish").json()

    def assign_schema(self, document_id: str, schema_id: str) -> dict:
        _, etag = self.get_document(document_id)
        return self._request(
            "PUT",
            f"/documents/{document_id}/schema",
            json={"schema_id": schema_id},
            headers={"If-Match": etag},
        ).json()

    def set_value(
        self, document_id: str, key: str, value, origin, source, confidence
    ) -> dict:
        _, etag = self.get_document(document_id)
        body = {"value": value, "origin": origin}
        if source:
            body["source"] = source
        if confidence is not None:
            body["confidence"] = confidence
        return self._request(
            "PUT",
            f"/documents/{document_id}/metadata/{key}",
            json=body,
            headers={"If-Match": etag},
        ).json()

    def verify_value(self, document_id: str, key: str) -> dict:
        _, etag = self.get_document(document_id)
        return self._request(
            "POST",
            f"/documents/{document_id}/metadata/{key}/verify",
            headers={"If-Match": etag},
        ).json()
