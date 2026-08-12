"""ContentStore invariants — pure filesystem, no database required."""

import hashlib
import io

import pytest

from archivum.content import InvalidKey, LocalFilesystemContentStore, key_for

DATA = b"synthetic demo bytes for a fictional municipality"


@pytest.fixture
def local_store(tmp_path):
    return LocalFilesystemContentStore(tmp_path / "cs")


def test_put_returns_hash_size_and_key(local_store):
    result = local_store.put(io.BytesIO(DATA))
    assert result.sha256 == hashlib.sha256(DATA).digest()
    assert result.size_bytes == len(DATA)
    assert result.key == key_for(result.sha256)
    assert local_store.exists(result.key)


def test_put_is_idempotent_for_identical_bytes(local_store, tmp_path):
    r1 = local_store.put(io.BytesIO(DATA))
    r2 = local_store.put(io.BytesIO(DATA))
    assert r1 == r2
    stored = [p for p in (tmp_path / "cs").rglob("*") if p.is_file()]
    assert len(stored) == 1


def test_open_roundtrips_bytes(local_store):
    result = local_store.put(io.BytesIO(DATA))
    with local_store.open(result.key) as f:
        assert f.read() == DATA


def test_verify_detects_corruption(local_store, tmp_path):
    result = local_store.put(io.BytesIO(DATA))
    assert local_store.verify(result.key, result.sha256)
    (tmp_path / "cs" / result.key).write_bytes(b"tampered")
    assert not local_store.verify(result.key, result.sha256)


def test_verify_missing_blob_is_false(local_store):
    assert not local_store.verify(key_for(hashlib.sha256(b"absent").digest()), b"\x00" * 32)


def test_delete(local_store):
    result = local_store.put(io.BytesIO(DATA))
    local_store.delete(result.key)
    assert not local_store.exists(result.key)


def test_failing_source_leaves_no_temp_or_final_files(local_store, tmp_path):
    class ExplodingSource:
        def __init__(self):
            self.calls = 0

        def read(self, n):
            self.calls += 1
            if self.calls > 1:
                raise OSError("simulated read failure")
            return b"partial chunk"

    with pytest.raises(OSError):
        local_store.put(ExplodingSource())
    leftovers = [p for p in (tmp_path / "cs").rglob("*") if p.is_file()]
    assert leftovers == []


def test_malformed_keys_rejected(local_store):
    for bad in ("../../etc/passwd", "ab/cd/short", "zz/zz/" + "z" * 64, ""):
        with pytest.raises(InvalidKey):
            local_store.exists(bad)
