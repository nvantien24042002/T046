"""Integrity/reconciliation tests use only temporary synthetic registries."""
import sqlite3

import pytest

from datasets.manager import DatasetManager
from storage.uploads import UploadStore, UploadError
from tests.test_integrated_api import integrated_api
from tests.test_validation_cancellation import setup_upload
from tests.test_cancellation_faults import InlineRunner, FaultDB, pending, snapshot


@pytest.mark.parametrize("field,value", [
    ("attempt_id", "22222222-2222-4222-8222-222222222222"),
    ("manifest_sha", "0"*64), ("index_sha", "0"*64), ("payload_sha", "0"*64),
    ("reservation", 1), ("attempt_state", "active"), ("association", "wrong-owner-upload"),
    ("archive_size", 1),
])
def test_each_ready_predicate_rejects_inconsistent_recovery(integrated_api, tmp_path, field, value):
    _, app, _, session, _ = setup_upload(integrated_api, tmp_path)
    manager, store = app.state.manager, app.state.store
    upload, cap = session["upload_id"], session["upload_capability"]
    manager.runner = InlineRunner()
    assert manager.validate("alice", upload, cap)["state"] == "ready"
    row = dict(manager._row(upload))
    if field == "association":
        # Test-owned DB only. Disable FK enforcement outside a transaction solely
        # to represent pre-existing corruption; no production policy is changed.
        store.db.execute("PRAGMA foreign_keys=OFF")
    with store.transaction():
        if field in {"attempt_id", "manifest_sha", "index_sha", "payload_sha"}:
            store.db.execute(f"UPDATE validation_datasets SET {field}=? WHERE upload_id=?", (value, upload))
        elif field in {"reservation", "attempt_state", "association"}:
            column = {"reservation":"reserved_bytes", "attempt_state":"state", "association":"upload_id"}[field]
            store.db.execute(f"UPDATE validation_attempts SET {column}=? WHERE id=?", (value, row["attempt_id"]))
        else:
            # Read-only predicate double avoids bypassing the payload-pin trigger.
            original_row = store._row
            def altered(identifier):
                result = dict(original_row(identifier))
                if identifier == upload:
                    result["size_bytes"] += value
                return result
            store._row = altered
    assert manager._ready_valid(manager._row(upload)) is False
    # Reconciliation must remove ready visibility before a reader is admitted.
    # A corrupt attempt association may instead fail closed with registry error.
    try:
        manager._recover()
    except sqlite3.Error:
        assert field in {"association", "attempt_id"}
        return
    assert manager._row(upload)["state"] != "ready"
    with pytest.raises(UploadError):
        with manager.reader("alice", upload, cap):
            pytest.fail("inconsistent publication became readable")


def test_committed_cancel_unreadable_ack_recovers_without_releasing_reservation(integrated_api, tmp_path):
    _, app, _, session, _ = setup_upload(integrated_api, tmp_path)
    store, manager, upload = app.state.store, app.state.manager, session["upload_id"]
    pending(app, upload)
    original = store.db
    class LostCancelReadback(FaultDB):
        applied = False
        def commit(self):
            self.db.commit()
            if self.matches:
                self.applied = True
                raise sqlite3.OperationalError("lost cancel acknowledgement")
        def execute(self, sql, *args):
            if self.applied and sql.startswith("SELECT * FROM validation_datasets"):
                raise sqlite3.OperationalError("cancel readback unavailable")
            return super().execute(sql, *args)
    store.db = LostCancelReadback(original, target="SET cancel_requested=1")
    try:
        with pytest.raises(sqlite3.OperationalError, match="readback unavailable"):
            manager.cancel("alice", upload, session["upload_capability"])
    finally:
        store.db = original
    row, job, leases = snapshot(store, upload)
    assert row["cancel_requested"] == 1 and row["state"] == "indexing"
    assert job["state"] == "active" and job["reserved_bytes"] == manager.limits.workspace_bytes
    assert leases == 0
    config = store.config
    store.close()
    reopened = UploadStore(config)
    try:
        recovered = DatasetManager(reopened)
        row, job, leases = snapshot(reopened, upload)
        assert recovered.status("alice", upload, session["upload_capability"])["state"] == "cancelled"
        assert job["state"] == "failed" and job["reserved_bytes"] == manager.limits.workspace_bytes
        assert leases == 0
    finally:
        reopened.close()
