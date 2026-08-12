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

    p = sub.add_parser("verify", help="verify a document's content hash")
    p.add_argument("path")

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
            if svc.verify_document(svc.resolve_path(args.path)):
                print("OK: stored content matches recorded sha256")
            else:
                print("FAILED: stored content does not match recorded sha256")
                return 2

    except DomainError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
