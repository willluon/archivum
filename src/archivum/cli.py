"""archivum CLI — a pure HTTP client of the archivum API (ADR-0010).

Every command except `serve` talks to the API at ARCHIVUM_API_URL (default
http://127.0.0.1:8000). Configuration: ARCHIVUM_ACTOR (actor display name,
default OS user); for `serve`: DATABASE_URL, ARCHIVUM_STORE_ROOT.
"""

import argparse
import getpass
import os
import sys

from archivum.client import ApiError, ApiUnreachable, ArchivumClient

FIELD_TYPES = ("text", "integer", "decimal", "boolean", "date", "datetime")
VALUE_ORIGINS = ("manual", "extracted", "imported", "system")


def _print_document(info: dict, etag: str) -> None:
    print(f"id: {info['id']}")
    print("type: document")
    print(f"title: {info['title']}")
    print(f"parent: {info['parent_id']}")
    print(f"revision: {info['revision']} (ETag {etag})")
    version = info["current_version"]
    print(f"version: {version['version_number']}")
    print(f"mime: {version['mime_type']}")
    print(f"size: {version['size_bytes']}")
    print(f"sha256: {version['sha256']}")
    if info.get("schema"):
        print(f"schema: {info['schema']['name']}")


def _print_folder(info: dict, etag: str) -> None:
    print(f"id: {info['id']}")
    print("type: folder")
    print(f"title: {info['title']}")
    print(f"parent: {info['parent_id']}")
    print(f"revision: {info['revision']} (ETag {etag})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="archivum", description="archivum API client")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("serve", help="run the archivum API server")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)

    p = sub.add_parser("mkdir", help="create a folder")
    p.add_argument("path")

    p = sub.add_parser("ingest", help="ingest a file as a new document")
    p.add_argument("file")
    p.add_argument("folder", help="destination folder path")
    p.add_argument("--title", help="document title (default: file name)")
    p.add_argument("--mime", default="application/octet-stream")

    p = sub.add_parser("ls", help="list a folder")
    p.add_argument("path", nargs="?", default="/")

    p = sub.add_parser("info", help="show an entry")
    p.add_argument("path")

    p = sub.add_parser("rename", help="rename an entry (identity unchanged)")
    p.add_argument("path")
    p.add_argument("new_title")
    p.add_argument("--expect", type=int, help="expected revision (If-Match)")

    p = sub.add_parser("mv", help="move an entry (identity unchanged)")
    p.add_argument("path")
    p.add_argument("dest_folder")
    p.add_argument("--expect", type=int, help="expected revision (If-Match)")

    p = sub.add_parser("audit", help="show a document's audit trail")
    p.add_argument("path")

    p = sub.add_parser("verify", help="verify every version's content hash")
    p.add_argument("path")

    p = sub.add_parser("versions", help="list a document's version history")
    p.add_argument("path")

    p = sub.add_parser("version-add", help="add a new immutable version")
    p.add_argument("path")
    p.add_argument("file")
    p.add_argument("--note", help="change note")
    p.add_argument("--expect", type=int, help="expected revision (If-Match)")
    p.add_argument("--mime", default="application/octet-stream")

    p = sub.add_parser("restore", help="restore a historical version as a new version")
    p.add_argument("path")
    p.add_argument("version_number", type=int)
    p.add_argument("--note")
    p.add_argument("--expect", type=int, help="expected revision (If-Match)")

    schema_p = sub.add_parser("schema", help="metadata schema management")
    schema_sub = schema_p.add_subparsers(dest="schema_command", required=True)
    p = schema_sub.add_parser("create", help="create a draft schema")
    p.add_argument("name")
    p.add_argument("--description")
    p = schema_sub.add_parser("field-add", help="add a field to a draft schema")
    p.add_argument("schema")
    p.add_argument("key")
    p.add_argument("type", choices=FIELD_TYPES)
    p.add_argument("--label")
    p.add_argument("--required", action="store_true")
    p = schema_sub.add_parser("publish", help="publish a draft schema (freezes structure)")
    p.add_argument("schema")
    p = schema_sub.add_parser("show", help="show a schema and its fields")
    p.add_argument("schema")

    meta_p = sub.add_parser("metadata", help="document metadata")
    meta_sub = meta_p.add_subparsers(dest="metadata_command", required=True)
    p = meta_sub.add_parser("assign", help="assign an active schema to a document")
    p.add_argument("path")
    p.add_argument("schema")
    p = meta_sub.add_parser("set", help="set a metadata value")
    p.add_argument("path")
    p.add_argument("key")
    p.add_argument("value")
    p.add_argument("--origin", choices=VALUE_ORIGINS, default="manual")
    p.add_argument("--source")
    p.add_argument("--confidence")
    p = meta_sub.add_parser("verify", help="verify a metadata value as a human")
    p.add_argument("path")
    p.add_argument("key")
    p = meta_sub.add_parser("show", help="show a document's metadata")
    p.add_argument("path")

    return parser


def run(args, client: ArchivumClient) -> int:
    if args.command == "mkdir":
        parent_path, _, title = args.path.rstrip("/").rpartition("/")
        parent = client.resolve(parent_path or "/")
        folder = client.create_folder(parent["id"], title)
        print(f"created folder {args.path} ({folder['id']})")

    elif args.command == "ingest":
        folder = client.resolve(args.folder)
        doc = client.ingest(folder["id"], args.file, args.title, args.mime)
        print(f"ingested {doc['title']} ({doc['id']})")

    elif args.command == "ls":
        folder = client.resolve(args.path)
        for row in client.children(folder["id"]):
            print(f"{row['type']:<9} {row['title']:<40} {row['id']}")

    elif args.command == "info":
        entry = client.resolve(args.path)
        if entry["type"] == "document":
            _print_document(*client.get_document(entry["id"]))
        else:
            _print_folder(*client.get_folder(entry["id"]))

    elif args.command == "rename":
        entry = client.resolve(args.path)
        client.patch_entry(entry["id"], entry["type"], args.expect, title=args.new_title)
        print(f"renamed ({entry['id']})")

    elif args.command == "mv":
        entry = client.resolve(args.path)
        dest = client.resolve(args.dest_folder)
        client.patch_entry(entry["id"], entry["type"], args.expect, parent_id=dest["id"])
        print(f"moved ({entry['id']})")

    elif args.command == "audit":
        entry = client.resolve(args.path)
        for event in client.audit(entry["id"]):
            print(
                f"{event['id']:>5}  {event['occurred_at']}  "
                f"{event['action']:<17} {event['details']}"
            )

    elif args.command == "verify":
        entry = client.resolve(args.path)
        results = client.verification(entry["id"])
        for number in sorted(results["versions"], key=int):
            print(f"v{number}: {'OK' if results['versions'][number] else 'FAILED'}")
        if not results["all_ok"]:
            return 2

    elif args.command == "versions":
        entry = client.resolve(args.path)
        for v in client.versions(entry["id"]):
            marker = "*" if v["is_current"] else "-"
            print(
                f"{marker} v{v['version_number']} {v['created_at']} "
                f"{v['size_bytes']} {v['sha256'][:16]} {v['change_note'] or ''}"
            )

    elif args.command == "version-add":
        entry = client.resolve(args.path)
        result = client.add_version(entry["id"], args.file, args.mime, args.note, args.expect)
        print(f"created version {result['version_number']}")

    elif args.command == "restore":
        entry = client.resolve(args.path)
        result = client.restore(entry["id"], args.version_number, args.note, args.expect)
        print(f"restored version {args.version_number} as version {result['version_number']}")

    elif args.command == "schema":
        if args.schema_command == "create":
            schema = client.create_schema(args.name, args.description)
            print(f"created draft schema {args.name!r} ({schema['id']})")
        elif args.schema_command == "field-add":
            schema = client.schema_by_name(args.schema)
            field = client.add_field(
                schema["id"], args.key, args.type, args.label, args.required
            )
            print(f"added field {args.key} ({args.type}) to {args.schema!r} ({field['id']})")
        elif args.schema_command == "publish":
            schema = client.schema_by_name(args.schema)
            client.publish_schema(schema["id"])
            print(f"published schema {args.schema!r} (structure frozen)")
        elif args.schema_command == "show":
            info = client.schema_by_name(args.schema)
            print(f"schema: {info['name']} ({info['state']}) {info['id']}")
            if info["description"]:
                print(f"description: {info['description']}")
            for f in info["fields"]:
                req = " required" if f["required"] else ""
                print(f"  {f['key']} ({f['field_type']}{req}) \"{f['label']}\"")

    elif args.command == "metadata":
        if args.metadata_command == "assign":
            entry = client.resolve(args.path)
            schema = client.schema_by_name(args.schema)
            client.assign_schema(entry["id"], schema["id"])
            print(f"assigned schema {args.schema!r}")
        elif args.metadata_command == "set":
            entry = client.resolve(args.path)
            client.set_value(
                entry["id"], args.key, args.value, args.origin, args.source, args.confidence
            )
            print(f"set {args.key}")
        elif args.metadata_command == "verify":
            entry = client.resolve(args.path)
            client.verify_value(entry["id"], args.key)
            print(f"verified {args.key}")
        elif args.metadata_command == "show":
            entry = client.resolve(args.path)
            info = client.metadata(entry["id"])
            if info["schema"] is None:
                print("no metadata schema assigned")
            else:
                print(f"schema: {info['schema']['name']} ({info['schema']['state']})")
                for v in info["values"]:
                    conf = v["confidence"] if v["confidence"] is not None else "-"
                    verified = "yes" if v["verified"] else "no"
                    src = v["source"] or "-"
                    print(
                        f"{v['key']}: {v['value']} (origin={v['origin']} "
                        f"confidence={conf} verified={verified} source={src})"
                    )
                if info["missing_required"]:
                    print(f"missing required: {', '.join(info['missing_required'])}")
                print(f"complete: {'yes' if info['complete'] else 'no'}")

    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "serve":
        import uvicorn

        from archivum.api import create_app

        uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")
        return 0

    client = ArchivumClient(actor=os.environ.get("ARCHIVUM_ACTOR") or getpass.getuser())
    try:
        return run(args, client)
    except (ApiError, ApiUnreachable) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
