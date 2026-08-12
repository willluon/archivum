"""ContentStore: content-addressed binary storage behind an interface (ADR-0003).

Keys are opaque, store-relative tokens derived from the SHA-256 of the bytes
("ab/cd/<64 hex>"). Callers never see absolute paths; keys never cross the
API boundary. Writes are blob-first, single-pass (hash while spooling),
temp-file + atomic rename, and idempotent for identical bytes.
"""

import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import BinaryIO, NamedTuple, Protocol

_CHUNK = 1024 * 1024
_KEY_RE = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{64}$")


class PutResult(NamedTuple):
    sha256: bytes
    size_bytes: int
    key: str


class ContentStoreError(Exception):
    pass


class BlobMissing(ContentStoreError):
    pass


class InvalidKey(ContentStoreError):
    pass


class ContentStore(Protocol):
    def put(self, source: BinaryIO) -> PutResult: ...

    def open(self, key: str) -> BinaryIO: ...

    def exists(self, key: str) -> bool: ...

    def verify(self, key: str, expected_sha256: bytes) -> bool: ...

    def delete(self, key: str) -> None: ...


def key_for(sha256: bytes) -> str:
    hex_digest = sha256.hex()
    return f"{hex_digest[:2]}/{hex_digest[2:4]}/{hex_digest}"


class LocalFilesystemContentStore:
    """Filesystem implementation. The root directory is configuration; the
    two-level fanout keeps directories small at any realistic blob count."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self._tmp = self.root / ".tmp"

    def _path(self, key: str) -> Path:
        # Keys are generated from hashes, never accepted from user input —
        # but validate anyway so a corrupted key can never traverse paths.
        if not _KEY_RE.fullmatch(key):
            raise InvalidKey(f"malformed storage key: {key!r}")
        return self.root / key

    def put(self, source: BinaryIO) -> PutResult:
        self._tmp.mkdir(parents=True, exist_ok=True)
        tmp = self._tmp / f"{uuid.uuid4().hex}.part"
        hasher = hashlib.sha256()
        size = 0
        try:
            with open(tmp, "wb") as out:
                while chunk := source.read(_CHUNK):
                    hasher.update(chunk)
                    size += len(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            digest = hasher.digest()
            key = key_for(digest)
            final = self._path(key)
            if final.exists():
                tmp.unlink()  # identical bytes already stored — idempotent
            else:
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(tmp, final)  # atomic on the same filesystem
            return PutResult(digest, size, key)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def open(self, key: str) -> BinaryIO:
        path = self._path(key)
        try:
            return open(path, "rb")
        except FileNotFoundError:
            raise BlobMissing(key) from None

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def verify(self, key: str, expected_sha256: bytes) -> bool:
        hasher = hashlib.sha256()
        try:
            with self.open(key) as f:
                while chunk := f.read(_CHUNK):
                    hasher.update(chunk)
        except BlobMissing:
            return False
        return hasher.digest() == expected_sha256

    def delete(self, key: str) -> None:
        # No kernel caller; exists for tests and future GC (V0.9).
        self._path(key).unlink(missing_ok=True)
