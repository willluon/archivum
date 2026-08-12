"""archivum CLI — the V0.1 demo surface over the repository kernel.

Configuration: DATABASE_URL (Postgres), ARCHIVUM_STORE_ROOT (blob directory,
default ./blobs), ARCHIVUM_ACTOR (principal display name, default OS user).
"""

import argparse
import getpass
import os
import sys
from pathlib import Path

from archivum.content import LocalFilesystemContentStore
from archivum.db import create_db_engine
from archivum.domain import DomainError
from archivum.identity import ensure_user_principal
from archivum.repository import RepositoryService


def _print_entry(info: dict) -> None:
    print(f"id: {info['id']}")
    print(f"type: {info['entry_type']}")
    print(f"title: {info['title']}")
    print(f"parent: {info['parent_id']}")
    if info["entry_type"] == "document":
        print(f"version: {info['version_number']}")
        print(f"mime: {info['mime_type']}")
        print(f"size: {info['size_bytes']}")
        print(f"sha256: {info['sha256'].hex()}")
        if info["original_filename"]:
            print(f"original_filename: {info['original_filename']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="archivum", description="archivum repository kernel")
    sub = parser.add_subparsers(dest="command", required=True)

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

    p = sub.add_parser("mv", help="move an entry (identity unchanged)")
    p.add_argument("path")
    p.add_argument("dest_folder")

    p = sub.add_parser("audit", help="show an entry's audit trail")
    p.add_argument("path")

    p = sub.add_parser("verify", help="verify every version's content hash")
    p.add_argument("path")

    p = sub.add_parser("versions", help="list a document's version history")
    p.add_argument("path")

    p = sub.add_parser("version-add", help="add a new immutable version")
    p.add_argument("path")
    p.add_argument("file")
    p.add_argument("--note", help="change note")
    p.add_argument("--expect", type=int, help="expected current version (optimistic concurrency)")
    p.add_argument("--mime", default="application/octet-stream")

    p = sub.add_parser("restore", help="restore a historical version as a new version")
    p.add_argument("path")
    p.add_argument("version_number", type=int)
    p.add_argument("--note", help="change note (default: 'restored from version N')")
    p.add_argument("--expect", type=int, help="expected current version (optimistic concurrency)")

    args = parser.parse_args(argv)

    engine = create_db_engine()
    store = LocalFilesystemContentStore(Path(os.environ.get("ARCHIVUM_STORE_ROOT", "blobs")))
    svc = RepositoryService(engine, store)
    actor = ensure_user_principal(
        engine, os.environ.get("ARCHIVUM_ACTOR") or getpass.getuser()
    )

    try:
        if args.command == "mkdir":
            parent_path, _, title = args.path.rstrip("/").rpartition("/")
            folder_id = svc.create_folder(actor, svc.resolve_path(parent_path or "/"), title)
            print(f"created folder {args.path} ({folder_id})")

        elif args.command == "ingest":
            file_path = Path(args.file)
            title = args.title or file_path.name
            with open(file_path, "rb") as f:
                doc_id = svc.ingest_document(
                    actor,
                    svc.resolve_path(args.folder),
                    f,
                    title,
                    mime_type=args.mime,
                    original_filename=file_path.name,
                )
            print(f"ingested {title} ({doc_id})")

        elif args.command == "ls":
            for row in svc.list_folder(svc.resolve_path(args.path)):
                print(f"{row['entry_type']:<9} {row['title']:<40} {row['id']}")

        elif args.command == "info":
            _print_entry(svc.get_entry(svc.resolve_path(args.path)))

        elif args.command == "rename":
            entry_id = svc.resolve_path(args.path)
            svc.rename(actor, entry_id, args.new_title)
            print(f"renamed ({entry_id})")

        elif args.command == "mv":
            entry_id = svc.resolve_path(args.path)
            svc.move(actor, entry_id, svc.resolve_path(args.dest_folder))
            print(f"moved ({entry_id})")

        elif args.command == "audit":
            for event in svc.audit_trail(svc.resolve_path(args.path)):
                print(
                    f"{event['id']:>5}  {event['occurred_at']}  "
                    f"{event['action']:<17} {event['details']}"
                )

        elif args.command == "verify":
            results = svc.verify_versions(svc.resolve_path(args.path))
            for number, ok in sorted(results.items()):
                print(f"v{number}: {'OK' if ok else 'FAILED'}")
            if not all(results.values()):
                return 2

        elif args.command == "versions":
            for v in svc.list_versions(svc.resolve_path(args.path)):
                marker = "*" if v["is_current"] else "-"
                print(
                    f"{marker} v{v['version_number']} {v['created_at'].isoformat()} "
                    f"{v['size_bytes']} {v['sha256'].hex()[:16]} {v['change_note'] or ''}"
                )

        elif args.command == "version-add":
            file_path = Path(args.file)
            with open(file_path, "rb") as f:
                result = svc.create_version(
                    actor,
                    svc.resolve_path(args.path),
                    f,
                    mime_type=args.mime,
                    original_filename=file_path.name,
                    change_note=args.note,
                    expected_version=args.expect,
                )
            print(f"created version {result['version_number']} ({result['version_id']})")

        elif args.command == "restore":
            result = svc.restore_version(
                actor,
                svc.resolve_path(args.path),
                args.version_number,
                change_note=args.note,
                expected_version=args.expect,
            )
            print(
                f"restored version {args.version_number} as version "
                f"{result['version_number']} ({result['version_id']})"
            )

    except DomainError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
