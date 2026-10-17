from dataclasses import replace
import hashlib
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import os
from uuid import uuid4

import pytest

from storage.config import UploadConfig
from storage.uploads import UploadError, UploadStore


@pytest.fixture
def config(tmp_path):
    return UploadConfig(tmp_path / "private", secrets.token_urlsafe(32), max_bytes=1024,
        chunk_bytes=8, block_bytes=3, min_free_bytes=0, quota_bytes=4096, idle_seconds=10, lifetime_seconds=30)


@pytest.fixture
def store(config):
    instance = UploadStore(config, clock=lambda: 100, free_bytes=lambda: 10**9)
    yield instance
    instance.close()


def create(store, size=16):
    return store.create("alice", "fixture.zip", size, str(uuid4()))


def put(store, session, data=b"abcdefgh", index=0, offset=0):
    writer = store.begin("alice", session["upload_id"], session["upload_capability"], index, offset,
                         hashlib.sha256(data).hexdigest())
    try:
        for start in range(0, len(data), 3):
            writer.write(data[start:start + 3])
        return writer.finish()
    finally:
        writer.abort()


def status(store, session):
    return store.status("alice", session["upload_id"], session["upload_capability"])


def test_durable_retry_receipt_and_seal(store):
    session = create(store)
    assert put(store, session)["received_bytes"] == 8
    retry = put(store, session)
    assert retry["replayed"] is True and retry["received_bytes"] == 8
    put(store, session, b"ABCDEFGH", 1, 8)
    done = store.complete("alice", session["upload_id"], session["upload_capability"])
    assert done["state"] == "uploaded" and done["ready"] is False and done["validated"] is False
    assert store.complete("alice", session["upload_id"], session["upload_capability"]) == done
    assert put(store, session)["replayed"] is True
    receipts = store.receipts("alice", session["upload_id"], session["upload_capability"], 0, 1)
    assert len(receipts["receipts"]) == 1 and receipts["next_index"] == 1
    assert store.db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 2
    assert store.db.execute("SELECT reserved_bytes FROM uploads").fetchone()[0] == 0


def test_interrupted_request_rollback_then_retry(store):
    session = create(store)
    writer = store.begin("alice", session["upload_id"], session["upload_capability"], 0, 0,
                         hashlib.sha256(b"abcdefgh").hexdigest())
    writer.write(b"abc")
    assert status(store, session)["received_bytes"] == 0
    assert store.maintain() == {"writer_active": True}
    with pytest.raises(UploadError, match="WRITER_BUSY"):
        store.cancel("alice", session["upload_id"], session["upload_capability"])
    writer.abort()
    assert status(store, session)["received_bytes"] == 0
    assert not store._file(session["upload_id"], "incoming.chunk").exists()
    assert put(store, session)["received_bytes"] == 8


@pytest.mark.parametrize("index,offset,digest,code", [
    (1, 0, "0"*64, "OFFSET_MISMATCH"), (0, 1, "0"*64, "OFFSET_MISMATCH"),
    (-1, 0, "0"*64, "INVALID_CHUNK_METADATA"), (0, 0, "bad", "INVALID_CHUNK_METADATA")])
def test_bad_chunk_metadata(store, index, offset, digest, code):
    session = create(store)
    with pytest.raises(UploadError, match=code):
        store.begin("alice", session["upload_id"], session["upload_capability"], index, offset, digest)


def test_hash_and_length_failures_never_ack(store):
    session = create(store)
    for data in (b"short", b"badbytes", b"too many bytes"):
        writer = store.begin("alice", session["upload_id"], session["upload_capability"], 0, 0,
                             hashlib.sha256(b"abcdefgh").hexdigest())
        try:
            with pytest.raises(UploadError):
                writer.write(data)
                writer.finish()
        finally:
            writer.abort()
        assert status(store, session)["received_bytes"] == 0
    put(store, session)
    with pytest.raises(UploadError, match="REPLAY_CONFLICT"):
        store.begin("alice", session["upload_id"], session["upload_capability"], 0, 0, "0"*64)
    assert store.db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1


def test_auth_idempotency_and_no_paths(store):
    request_key = str(uuid4())
    session = store.create("alice", "fixture.zip", 16, request_key)
    assert store.create("alice", "fixture.zip", 16, request_key) == session
    with pytest.raises(UploadError, match="IDEMPOTENCY_CONFLICT"):
        store.create("alice", "other.zip", 16, request_key)
    for owner, capability in [("bob", session["upload_capability"]), ("alice", "wrong")]:
        with pytest.raises(UploadError, match="ACCESS_DENIED"):
            store.status(owner, session["upload_id"], capability)
    with pytest.raises(UploadError, match="UNKNOWN_UPLOAD"):
        store.status("alice", "../../outside", "wrong")
    assert str(store.root) not in str(session)
    assert session["upload_capability"] not in str(dict(store.db.execute("SELECT * FROM uploads").fetchone()))


def test_capacity_reservation_and_quota(config):
    with pytest.raises(UploadError, match="INSUFFICIENT_STORAGE"):
        instance = UploadStore(config, free_bytes=lambda: 20)
        try:
            create(instance)  # Needs 16 + 8 reserved bytes, not just current file size.
        finally:
            instance.close()
    instance = UploadStore(replace(config, quota_bytes=20), free_bytes=lambda: 10**9)
    try:
        session = create(instance, 8)
        assert instance.db.execute("SELECT reserved_bytes FROM uploads").fetchone()[0] == 16
        put(instance, session)
        instance.complete("alice", session["upload_id"], session["upload_capability"])
        with pytest.raises(UploadError, match="STORAGE_QUOTA"):
            create(instance, 16)
        instance.cancel("alice", session["upload_id"], session["upload_capability"])
        assert create(instance, 16)["received_bytes"] == 0
    finally:
        instance.close()


def test_expiry_does_not_refresh_on_status_and_cleans_owned_files(config):
    clock = [100.0]
    instance = UploadStore(config, clock=lambda: clock[0], free_bytes=lambda: 10**9)
    try:
        session = create(instance)
        clock[0] = 109
        put(instance, session)
        clock[0] = 118
        assert status(instance, session)["expires_at"] == 119
        clock[0] = 120
        instance.maintain()
        result = status(instance, session)
        assert result["state"] == "expired" and result["durable"] is False
        assert result["cleanup_pending"] is False
        assert not instance._folder(session["upload_id"]).exists()
        assert instance.db.execute("SELECT reserved_bytes FROM uploads").fetchone()[0] == 0
    finally:
        instance.close()


def test_unknown_orphans_and_unknown_session_contents_are_preserved(store):
    orphan = store.uploads_dir / "unrelated-user-file"
    orphan.write_text("do not delete", encoding="utf-8")
    assert store.maintain()["unknown_entries_preserved"] == 1
    assert orphan.read_text() == "do not delete"
    session = create(store)
    unexpected = store._folder(session["upload_id"]) / "user-notes.txt"
    unexpected.write_text("preserve", encoding="utf-8")
    result = store.cancel("alice", session["upload_id"], session["upload_capability"])
    assert result["cleanup_pending"] is True
    assert unexpected.read_text() == "preserve"
    assert store._file(session["upload_id"], "payload.part").exists()
    assert store.db.execute("SELECT reserved_bytes FROM uploads").fetchone()[0] > 0


def test_unacknowledged_tail_and_scratch_recovery(config):
    instance = UploadStore(config, free_bytes=lambda: 10**9)
    session = create(instance)
    put(instance, session)
    payload = instance._file(session["upload_id"], "payload.part")
    with payload.open("ab") as file:
        file.write(b"uncommitted")
    instance._file(session["upload_id"], "incoming.chunk").write_bytes(b"partial")
    instance.close()
    recovered = UploadStore(config, free_bytes=lambda: 10**9)
    try:
        assert payload.stat().st_size == 8
        assert not recovered._file(session["upload_id"], "incoming.chunk").exists()
        assert status(recovered, session)["received_bytes"] == 8
        assert put(recovered, session)["replayed"] is True
    finally:
        recovered.close()


@pytest.mark.parametrize("damage", ["missing", "short", "same_size_corrupt", "db_offset", "receipt_offset"])
def test_file_sqlite_inconsistency_fails_closed_without_truncating(config, damage):
    instance = UploadStore(config, free_bytes=lambda: 10**9)
    session = create(instance)
    put(instance, session)
    payload = instance._file(session["upload_id"], "payload.part")
    if damage == "missing":
        payload.unlink()  # Synthetic test-owned data only.
    elif damage == "short":
        payload.write_bytes(b"abc")
    elif damage == "same_size_corrupt":
        payload.write_bytes(b"XXXXXXXX")
    elif damage == "db_offset":
        instance.db.execute("UPDATE uploads SET committed_bytes=0")
    else:
        instance.db.execute("UPDATE receipts SET offset_bytes=1")
    before = payload.stat().st_size if payload.exists() else None
    instance.close()
    recovered = UploadStore(config, free_bytes=lambda: 10**9)
    try:
        result = status(recovered, session)
        assert result["state"] == "failed" and not result["durable"]
        assert result["next_offset"] is None and result["received_bytes"] == 0
        assert (payload.stat().st_size if payload.exists() else None) == before
        assert recovered.db.execute("SELECT reserved_bytes FROM uploads").fetchone()[0] > 0
    finally:
        recovered.close()


@pytest.mark.parametrize("point,error", [("payload_fsync", OSError), ("before_receipt_commit", sqlite3.OperationalError),
                                         ("receipt_commit", OSError)])
def test_ambiguous_errors_use_sqlite_offset(store, point, error):
    session = create(store)
    def fault(where):
        if where == point:
            store.fault = lambda p: None
            raise error("synthetic failure")
    store.fault = fault
    with pytest.raises(error):
        put(store, session)
    expected = 8 if point == "receipt_commit" else 0
    assert status(store, session)["received_bytes"] == expected
    assert store._file(session["upload_id"], "payload.part").stat().st_size == expected
    assert put(store, session)["received_bytes"] == 8


def test_fsync_failure_does_not_create_receipt(store, monkeypatch):
    import storage.uploads as uploads
    session = create(store)
    real_sync = uploads.sync_file
    def fail_once(file):
        monkeypatch.setattr(uploads, "sync_file", real_sync)
        raise OSError("simulated fsync failure")
    monkeypatch.setattr(uploads, "sync_file", fail_once)
    with pytest.raises(OSError):
        put(store, session)
    assert status(store, session)["received_bytes"] == 0
    assert store.db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0
    assert put(store, session)["received_bytes"] == 8


def test_actual_scratch_write_error_rolls_back_and_can_retry(store):
    session = create(store)
    writer = store.begin("alice", session["upload_id"], session["upload_capability"], 0, 0,
                         hashlib.sha256(b"abcdefgh").hexdigest())
    class PartialWriteFailure:
        def __init__(self, file):
            self.file = file
        def __getattr__(self, attr):
            return getattr(self.file, attr)
        def write(self, data):
            self.file.write(data[:1])
            raise OSError(28, "synthetic disk full during write")
    writer.scratch = PartialWriteFailure(writer.scratch)
    try:
        with pytest.raises(OSError):
            writer.write(b"abcdefgh")
    finally:
        writer.abort()
    assert status(store, session)["received_bytes"] == 0
    assert store.db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0
    assert put(store, session)["received_bytes"] == 8


@pytest.mark.parametrize("commit_happened", [False, True])
def test_sqlite_commit_exception_before_or_after_real_commit(store, commit_happened):
    session = create(store)
    real_connection = store.db
    class AmbiguousCommit:
        triggered = False
        def __getattr__(self, attr):
            return getattr(real_connection, attr)
        def commit(self):
            if not self.triggered:
                self.triggered = True
                if commit_happened:
                    real_connection.commit()
                raise sqlite3.OperationalError("synthetic COMMIT result lost")
            real_connection.commit()
    store.db = AmbiguousCommit()
    with pytest.raises(sqlite3.OperationalError):
        put(store, session)
    assert status(store, session)["received_bytes"] == (8 if commit_happened else 0)
    assert put(store, session)["received_bytes"] == 8


def test_hardlinked_payload_is_not_modified_or_cleaned(config, tmp_path):
    instance = UploadStore(config, free_bytes=lambda: 10**9)
    session = create(instance)
    payload = instance._file(session["upload_id"], "payload.part")
    payload.unlink()  # Replace only this synthetic, empty service-created fixture.
    outside = tmp_path / "unrelated-user-note.txt"
    outside.write_bytes(b"preserve unrelated data")
    os.link(outside, payload)
    instance.close()
    recovered = UploadStore(config, free_bytes=lambda: 10**9)
    try:
        assert status(recovered, session)["state"] == "failed"
        result = recovered.cancel("alice", session["upload_id"], session["upload_capability"])
        assert result["cleanup_pending"]
        assert outside.read_bytes() == b"preserve unrelated data"
    finally:
        recovered.close()


def test_absolute_expiry_is_not_extended_by_chunk_commits(config):
    clock = [100.0]
    instance = UploadStore(config, clock=lambda: clock[0], free_bytes=lambda: 10**9)
    try:
        session = create(instance, 32)
        for index, now in enumerate([109, 118, 127]):
            clock[0] = now
            put(instance, session, index=index, offset=index*8)
        assert status(instance, session)["expires_at"] == 130
        clock[0] = 130
        instance.maintain()
        assert status(instance, session)["state"] == "expired"
    finally:
        instance.close()


def test_root_lock_secret_change_and_nonempty_adoption(config, tmp_path):
    instance = UploadStore(config)
    try:
        with pytest.raises(OSError):
            UploadStore(config)
    finally:
        instance.close()
    with pytest.raises(ValueError, match="mismatch"):
        UploadStore(replace(config, secret=secrets.token_urlsafe(32)))
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    note = unrelated / "notes.txt"
    note.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="empty private"):
        UploadStore(replace(config, root=unrelated))
    assert list(unrelated.iterdir()) == [note]


CHILD = r'''
import hashlib, os, sys
from pathlib import Path
from storage.config import UploadConfig
from storage.uploads import UploadStore
config = UploadConfig(Path(sys.argv[1]), os.environ['CVAT_TEST_SECRET'], max_bytes=1024,
    chunk_bytes=8, block_bytes=3, min_free_bytes=0, quota_bytes=4096)
store = UploadStore(config, fault=lambda point: os._exit(73) if point == sys.argv[3] else None)
upload_id = sys.argv[2]
writer = store.begin('alice', upload_id, store.capability('alice', upload_id), 0, 0,
                     hashlib.sha256(b'abcdefgh').hexdigest())
writer.write(b'abcdefgh')
writer.finish()
os._exit(0)
'''


@pytest.mark.parametrize("point,expected", [("scratch_fsync", 0), ("payload_fsync", 0),
                                          ("before_receipt_commit", 0), ("receipt_commit", 8)])
def test_actual_process_crash_at_durability_boundaries(config, point, expected):
    instance = UploadStore(config, free_bytes=lambda: 10**9)
    session = create(instance)
    instance.close()
    process = subprocess.run([sys.executable, "-c", CHILD, str(config.root), session["upload_id"], point],
        env={**os.environ, "CVAT_TEST_SECRET": config.secret}, capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, timeout=20)
    assert process.returncode == 73, process.stderr
    recovered = UploadStore(config, free_bytes=lambda: 10**9)
    try:
        assert status(recovered, session)["received_bytes"] == expected
        assert recovered._file(session["upload_id"], "payload.part").stat().st_size == expected
        assert put(recovered, session)["received_bytes"] == 8
    finally:
        recovered.close()


CANCEL_CHILD = r'''
import os, sys
from pathlib import Path
from storage.config import UploadConfig
from storage.uploads import UploadStore
config = UploadConfig(Path(sys.argv[1]), os.environ['CVAT_TEST_SECRET'], max_bytes=1024,
    chunk_bytes=8, block_bytes=3, min_free_bytes=0, quota_bytes=4096)
store = UploadStore(config, fault=lambda point: os._exit(73) if point == sys.argv[3] else None)
if sys.argv[3] == 'session_commit':
    store.create('alice', 'fixture.zip', 16, sys.argv[2])
else:
    store.cancel('alice', sys.argv[2], store.capability('alice', sys.argv[2]))
'''


@pytest.mark.parametrize("point", ["cancel_commit", "files_removed"])
def test_cancel_crash_cleanup_releases_reservation_only_after_removal(config, point):
    instance = UploadStore(config, free_bytes=lambda: 10**9)
    session = create(instance)
    put(instance, session)
    instance.close()
    process = subprocess.run([sys.executable, "-c", CANCEL_CHILD, str(config.root), session["upload_id"], point],
        env={**os.environ, "CVAT_TEST_SECRET": config.secret}, capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, timeout=20)
    assert process.returncode == 73, process.stderr
    recovered = UploadStore(config, free_bytes=lambda: 10**9)
    try:
        result = status(recovered, session)
        assert result["state"] == "cancelled" and not result["cleanup_pending"]
        assert not recovered._folder(session["upload_id"]).exists()
        assert recovered.db.execute("SELECT reserved_bytes FROM uploads").fetchone()[0] == 0
    finally:
        recovered.close()


def test_crash_after_creation_reservation_recovers_without_duplicate_session(config):
    request_key = str(uuid4())
    process = subprocess.run([sys.executable, "-c", CANCEL_CHILD, str(config.root), request_key, "session_commit"],
        env={**os.environ, "CVAT_TEST_SECRET": config.secret}, capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, timeout=20)
    assert process.returncode == 73, process.stderr
    recovered = UploadStore(config, free_bytes=lambda: 10**9)
    try:
        session = recovered.create("alice", "fixture.zip", 16, request_key)
        assert recovered._file(session["upload_id"], "payload.part").stat().st_size == 0
        assert recovered.db.execute("SELECT COUNT(*) FROM uploads").fetchone()[0] == 1
    finally:
        recovered.close()
