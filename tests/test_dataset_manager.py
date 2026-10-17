import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import uuid

import pytest

from datasets.limits import Limits, ValidationError
from datasets.manager import DatasetManager
from datasets.runner import WorkerRunner
from storage.config import UploadConfig
from storage.uploads import UploadError, UploadStore
from tests.fixtures import make_zip


@pytest.fixture
def make_store(tmp_path):
    def _create(max_bytes=100000, quota_bytes=10**9):
        root = tmp_path / f"store_{uuid.uuid4().hex[:8]}"
        config = UploadConfig(
            root,
            secrets.token_urlsafe(32),
            max_bytes=max_bytes,
            chunk_bytes=16384,
            block_bytes=4096,
            min_free_bytes=0,
            quota_bytes=quota_bytes,
            idle_seconds=10,
            lifetime_seconds=60,
        )
        return UploadStore(config, clock=lambda: 100, free_bytes=lambda: 10**10)
    return _create


def upload_fixture(store, owner="alice", filename="fixture.zip", image_only=False):
    archive_path = store.root.parent / f"tmp_{uuid.uuid4().hex[:8]}.zip"
    make_zip(archive_path, image_only=image_only)
    data = archive_path.read_bytes()
    session = store.create(owner, filename, len(data), str(uuid.uuid4()))
    writer = store.begin(owner, session["upload_id"], session["upload_capability"], 0, 0, hashlib.sha256(data).hexdigest())
    writer.write(data)
    writer.finish()
    store.complete(owner, session["upload_id"], session["upload_capability"])
    archive_path.unlink()
    return session, data


def test_manager_init_and_format_marker(make_store):
    store = make_store()
    try:
        manager = DatasetManager(store, Limits())
        assert (store.root / ".datasets-v1" / "format.json").exists()
        assert (store.root / ".datasets-v1" / "work").exists()
        assert (store.root / ".datasets-v1" / "sealed").exists()

        # Reopening manager on same store raises
        with pytest.raises(ValidationError) as exc:
            DatasetManager(store, Limits())
        assert "MANAGER_ALREADY_OPEN" in str(exc.value)
    finally:
        store.close()


def test_validation_workflow_and_reader(make_store):
    store = make_store()
    try:
        session, data = upload_fixture(store)
        upload_id = session["upload_id"]
        cap = session["upload_capability"]

        manager = DatasetManager(store, Limits())

        # Status before validation
        st = manager.status("alice", upload_id, cap)
        assert st["state"] == "not_requested"
        assert st["ready"] is False

        # Validate
        res = manager.validate("alice", upload_id, cap)
        assert res["state"] == "ready"
        assert res["ready"] is True
        assert res["error_code"] is None
        assert res["dataset_id"] is not None

        # Status after validation
        st = manager.status("alice", upload_id, cap)
        assert st["state"] == "ready"
        assert st["ready"] is True

        # Reader context
        with manager.reader("alice", upload_id, cap) as reader:
            manifest = reader["summary"]
            assert manifest["dataset_id"] == res["dataset_id"]
            assert manifest["validated"] is True
            assert manifest["images"] == 2
            assert manifest["annotations"] is True

            # Query index
            entries_count = reader["index"].execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            assert entries_count == 3
            xml_count = reader["index"].execute("SELECT COUNT(*) FROM xml_nodes").fetchone()[0]
            assert xml_count > 0

            # Active lease exists during reader
            active_leases = store.db.execute("SELECT COUNT(*) FROM validation_leases").fetchone()[0]
            assert active_leases == 1

        # Lease released on exit
        active_leases = store.db.execute("SELECT COUNT(*) FROM validation_leases").fetchone()[0]
        assert active_leases == 0
    finally:
        store.close()


def test_validation_rejects_unsealed_upload(make_store):
    store = make_store()
    try:
        session = store.create("alice", "test.zip", 100, str(uuid.uuid4()))
        manager = DatasetManager(store, Limits())
        with pytest.raises(UploadError) as exc:
            manager.validate("alice", session["upload_id"], session["upload_capability"])
        assert exc.value.status == 409
        assert exc.value.code == "UPLOAD_NOT_SEALED"
    finally:
        store.close()


def test_reader_rejects_unready_dataset(make_store):
    store = make_store()
    try:
        session, _ = upload_fixture(store)
        manager = DatasetManager(store, Limits())
        with pytest.raises(UploadError) as exc:
            with manager.reader("alice", session["upload_id"], session["upload_capability"]):
                pass
        assert exc.value.status == 409
        assert exc.value.code == "DATASET_NOT_READY"
    finally:
        store.close()


def test_validation_payload_pinning_triggers(make_store):
    store = make_store()
    try:
        session, _ = upload_fixture(store)
        upload_id = session["upload_id"]
        cap = session["upload_capability"]

        manager = DatasetManager(store, Limits())
        manager.validate("alice", upload_id, cap)

        # Trigger validation_payload_pin: attempt to cancel upload directly in store
        with pytest.raises(sqlite3.IntegrityError, match="DATASET_PAYLOAD_PINNED"):
            store.cancel("alice", upload_id, cap)

        # Direct SQL update to cancelled should abort with trigger message
        with pytest.raises(sqlite3.IntegrityError) as exc:
            with store.transaction():
                store.db.execute("UPDATE uploads SET state='cancelled' WHERE id=?", (upload_id,))
        assert "DATASET_PAYLOAD_PINNED" in str(exc.value)

        # Direct SQL delete should abort with trigger message
        with pytest.raises(sqlite3.IntegrityError) as exc:
            with store.transaction():
                store.db.execute("DELETE FROM uploads WHERE id=?", (upload_id,))
        assert "DATASET_PAYLOAD_PINNED" in str(exc.value)
    finally:
        store.close()


def test_validation_cancellation(make_store):
    store = make_store()
    try:
        session, _ = upload_fixture(store)
        upload_id = session["upload_id"]
        cap = session["upload_capability"]

        manager = DatasetManager(store, Limits())
        # Cancel before validate finishes using cancelled callback
        res = manager.validate("alice", upload_id, cap, cancelled=lambda: True)
        assert res["state"] == "cancelled"
        assert res["ready"] is False
        assert res["error_code"] == "VALIDATION_CANCELLED"

        # Calling cancel on non-ready dataset is idempotent
        st = manager.cancel("alice", upload_id, cap)
        assert st["state"] == "cancelled"
    finally:
        store.close()


def test_recovery_interrupted_worker(make_store):
    store = make_store()
    config = store.config
    try:
        session, _ = upload_fixture(store)
        upload_id = session["upload_id"]
        cap = session["upload_capability"]

        manager = DatasetManager(store, Limits())
        attempt = str(uuid.uuid4())
        dataset_id = str(uuid.uuid4())

        # Simulate a crash leaving dataset in 'validating'
        with store.transaction():
            store.db.execute(
                "INSERT INTO validation_datasets (upload_id,dataset_id,attempt_id,state) VALUES (?,?,?,'validating')",
                (upload_id, dataset_id, attempt)
            )
            store.db.execute(
                "INSERT INTO validation_attempts VALUES (?,?,'active',?)",
                (attempt, upload_id, 1024)
            )
    finally:
        store.close()

    # Reopen store and manager to trigger _recover()
    store2 = UploadStore(config, clock=lambda: 100, free_bytes=lambda: 10**10)
    try:
        manager2 = DatasetManager(store2, Limits())
        row = manager2._row(upload_id)
        assert row["state"] == "failed"
        assert row["error_code"] == "WORKER_INTERRUPTED"
    finally:
        store2.close()
