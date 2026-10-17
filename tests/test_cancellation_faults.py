"""Real temporary SQLite transactions and controlled worker fault boundaries."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import sqlite3
import threading
import uuid

import pytest

from datasets.limits import Limits, ValidationError
from datasets.manager import DatasetManager
from datasets.runner import WorkerRunner, WorkerUnconfirmed
from datasets.validate import validate_archive
from storage.uploads import UploadError, UploadStore
from tests.test_integrated_api import integrated_api, auth
from tests.test_validation_cancellation import setup_upload, GatedRunner, attempt


class InlineRunner:
    """Tiny test archives only; process behavior is tested separately."""
    def run(self, payload, work, dataset_id, limits, **kwargs):
        validate_archive(payload, work, dataset_id, limits)


def snapshot(store, upload):
    with closing(sqlite3.connect((store.root / "registry.sqlite3").as_uri()+"?mode=ro",uri=True)) as db:
        db.row_factory = sqlite3.Row
        row = dict(db.execute("SELECT * FROM validation_datasets WHERE upload_id=?",(upload,)).fetchone())
        job = dict(db.execute("SELECT * FROM validation_attempts WHERE id=?",(row["attempt_id"],)).fetchone())
        leases = db.execute("SELECT COUNT(*) FROM validation_leases").fetchone()[0]
    return row,job,leases


def pending(app, upload, phase="indexing"):
    manager,store = app.state.manager,app.state.store
    with store.guard,store.transaction():
        job = str(uuid.uuid4())
        store.db.execute("INSERT INTO validation_datasets(upload_id,dataset_id,attempt_id,state) VALUES (?,?,?,?)",
                         (upload,str(uuid.uuid4()),job,phase))
        store.db.execute("INSERT INTO validation_attempts VALUES (?,?,'active',?)",
                         (job,upload,manager.limits.workspace_bytes))


class FaultDB:
    def __init__(self, db, *, update=None, commit=None, rollback=None, target=None, barrier=None):
        self.db,self.update,self.commit_fault,self.rollback_fault = db,update,commit,rollback
        self.target,self.barrier,self.matches,self.updates = target,barrier,False,0
    def __getattr__(self, name):
        return getattr(self.db,name)
    def execute(self, sql, *args):
        if sql.startswith("UPDATE"):
            self.updates += 1
            if self.update == self.updates:
                raise sqlite3.OperationalError("injected update failure")
            if self.target and self.target in sql:
                self.matches = True
        return self.db.execute(sql,*args)
    def commit(self):
        selected = self.target is None or self.matches
        if selected and self.commit_fault == "before":
            raise sqlite3.OperationalError("injected commit failure")
        self.db.commit()
        if selected and self.barrier:
            entered,release = self.barrier
            entered.set()
            assert release.wait(10)
        if selected and self.commit_fault == "after":
            raise sqlite3.OperationalError("injected lost commit acknowledgement")
        self.matches = False
    def rollback(self):
        if self.rollback_fault == "before":
            raise sqlite3.OperationalError("injected rollback failure")
        self.db.rollback()
        if self.rollback_fault == "after":
            raise sqlite3.OperationalError("injected lost rollback acknowledgement")


@pytest.mark.parametrize("fault", [dict(update=1),dict(update=2),dict(commit="before"),
                                   dict(commit="before",rollback="after"),dict(commit="after")])
def test_terminal_transaction_faults(integrated_api,tmp_path,fault):
    client,app,owners,session,adapter = setup_upload(integrated_api,tmp_path)
    store,manager,upload = app.state.store,app.state.manager,session["upload_id"]
    pending(app,upload)
    original = store.db
    store.db = FaultDB(original,**fault)
    try:
        if fault.get("commit") == "after":
            manager._failed(upload,"VALIDATION_CANCELLED")
        else:
            with pytest.raises(sqlite3.OperationalError):
                manager._failed(upload,"VALIDATION_CANCELLED")
    finally:
        store.db = original
    row,job,leases = snapshot(store,upload)
    applied = fault.get("commit") == "after"
    assert (row["state"],job["state"]) == (("cancelled","failed") if applied else ("indexing","active"))
    assert job["reserved_bytes"] == manager.limits.workspace_bytes and leases == 0
    assert not original.in_transaction


def test_failed_rollback_quarantines_connection(integrated_api,tmp_path):
    client,app,owners,session,adapter = setup_upload(integrated_api,tmp_path)
    store,manager,upload = app.state.store,app.state.manager,session["upload_id"]
    pending(app,upload)
    original = store.db
    store.db = FaultDB(original,commit="before",rollback="before")
    try:
        with pytest.raises(sqlite3.OperationalError):
            manager._failed(upload,"VALIDATION_CANCELLED")
        assert original.in_transaction
        row,job,_ = snapshot(store,upload)
        assert (row["state"],job["state"]) == ("indexing","active")
        assert job["reserved_bytes"] == manager.limits.workspace_bytes
        for operation in [lambda: manager.status("alice",upload,session["upload_capability"]),
                          lambda: manager.validate("alice",upload,session["upload_capability"],retry=True),
                          lambda: manager.cleanup_attempt("alice",upload,session["upload_capability"],job["id"])]:
            with pytest.raises(sqlite3.Error):
                operation()
    finally:
        store.db = original
        original.rollback()  # Test-owned database only; production stays fail-closed.


@pytest.mark.parametrize("point", ["validated","index_sealed","publishing_commit","directory_rename","ready_commit"])
def test_publication_faults_follow_committed_state(integrated_api,tmp_path,point):
    client,app,owners,session,adapter = setup_upload(integrated_api,tmp_path)
    manager,upload = app.state.manager,session["upload_id"]
    manager.runner = InlineRunner()
    def fault(stage):
        if stage == point:
            raise OSError("injected publication failure")
    manager.fault = fault
    result = manager.validate("alice",upload,session["upload_capability"])
    row,job,leases = snapshot(app.state.store,upload)
    if point == "ready_commit":
        assert result["state"] == "ready" and manager._ready_valid(manager._row(upload))
        assert job["state"] == "published" and job["reserved_bytes"] == 0
    else:
        assert result["state"] == "failed" and result["error_code"] == "PUBLICATION_IO_OR_REGISTRY_FAILED"
        assert job["state"] == "failed" and job["reserved_bytes"] == manager.limits.workspace_bytes
        with pytest.raises(UploadError):
            with manager.reader("alice",upload,session["upload_capability"]):
                pytest.fail("unpublished data became readable")
    assert leases == 0


@pytest.mark.parametrize("commit", ["before","after"])
@pytest.mark.parametrize("recovery_fault", ["hook","commit"])
def test_ready_commit_ack_and_restart_reconcile(integrated_api,tmp_path,commit,recovery_fault):
    client,app,owners,session,adapter = setup_upload(integrated_api,tmp_path)
    store,manager,upload = app.state.store,app.state.manager,session["upload_id"]
    manager.runner = InlineRunner()
    original = store.db
    store.db = FaultDB(original,commit=commit,target="SET state='ready'")
    try:
        if commit == "before":
            with pytest.raises(sqlite3.OperationalError):
                manager.validate("alice",upload,session["upload_capability"])
        else:
            assert manager.validate("alice",upload,session["upload_capability"])["state"] == "ready"
    finally:
        store.db = original
    row,job,_ = snapshot(store,upload)
    assert (row["state"],job["state"]) == (("publishing","active") if commit == "before" else ("ready","published"))
    config = store.config
    store.close()
    reopened = UploadStore(config)
    try:
        if recovery_fault == "commit":
            reopened.db = FaultDB(reopened.db,commit="after",target="SET state='ready'")
        def after_commit(point):
            if point == "ready_commit" and recovery_fault == "hook":
                raise OSError("injected recovery post-commit failure")
        recovered = DatasetManager(reopened,fault=after_commit)
        assert recovered.status("alice",upload,session["upload_capability"])["state"] == "ready"
        row,job,leases = snapshot(reopened,upload)
        assert job["state"] == "published" and job["reserved_bytes"] == 0 and leases == 0
    finally:
        reopened.close()


@pytest.mark.parametrize("content", ["{}","[]","null"])
def test_committed_ready_with_bad_seal_is_not_served(integrated_api,tmp_path,content):
    client,app,owners,session,adapter = setup_upload(integrated_api,tmp_path)
    manager,upload = app.state.manager,session["upload_id"]
    manager.runner = InlineRunner()
    def fault(point):
        if point == "ready_commit":
            row = manager._row(upload)
            (manager._path("sealed",row["attempt_id"])/"manifest.json").write_text(content)
            raise OSError("injected seal corruption after commit")
    manager.fault = fault
    assert manager.validate("alice",upload,session["upload_capability"])["state"] == "failed"
    with pytest.raises(UploadError):
        with manager.reader("alice",upload,session["upload_capability"]):
            pytest.fail("bad seal became readable")


@pytest.mark.parametrize("boundary", ["cancel","ready"])
def test_cancel_race_barrier_is_after_actual_commit(integrated_api,tmp_path,boundary):
    client,app,owners,session,adapter = setup_upload(integrated_api,tmp_path)
    manager,store,upload = app.state.manager,app.state.store,session["upload_id"]
    runner = GatedRunner()
    manager.runner = runner
    entered,release = threading.Event(),threading.Event()
    original = store.db
    with ThreadPoolExecutor(2) as executor:
        validation = executor.submit(manager.validate,"alice",upload,session["upload_capability"])
        try:
            assert runner.entered.wait(5)
            if boundary == "cancel":
                store.db = FaultDB(original,commit="after",target="SET cancel_requested=1",barrier=(entered,release))
                cancellation = executor.submit(manager.cancel,"alice",upload,session["upload_capability"])
                assert entered.wait(5)
                row,job,_ = snapshot(store,upload)
                assert row["cancel_requested"] == 1 and job["state"] == "active"
                runner.release.set()
            else:
                store.db = FaultDB(original,commit="after",target="SET state='ready'",barrier=(entered,release))
                runner.release.set()
                assert entered.wait(5)
                row,job,_ = snapshot(store,upload)
                assert row["state"] == "ready" and job["state"] == "published" and job["reserved_bytes"] == 0
                cancellation = executor.submit(manager.cancel,"alice",upload,session["upload_capability"])
        finally:
            runner.release.set()
            release.set()
        if boundary == "cancel":
            assert cancellation.result(timeout=5)["state"] == "indexing"
            assert validation.result(timeout=10)["state"] == "cancelled"
        else:
            assert validation.result(timeout=10)["state"] == "ready"
            with pytest.raises(UploadError) as caught:
                cancellation.result(timeout=5)
            assert caught.value.code == "NO_CANCELLABLE_VALIDATION"
    store.db = original


def test_uncertain_worker_blocks_terminal_retry_and_cleanup(integrated_api,tmp_path):
    client,app,owners,session,adapter = setup_upload(integrated_api,tmp_path)
    manager,upload = app.state.manager,session["upload_id"]
    class Uncertain:
        def run(self,*args,**kwargs):
            raise WorkerUnconfirmed("WORKER_STOP_UNCONFIRMED")
    manager.runner = Uncertain()
    with pytest.raises(WorkerUnconfirmed):
        manager.validate("alice",upload,session["upload_capability"])
    row,job,_ = snapshot(app.state.store,upload)
    assert (row["state"],job["state"]) == ("validating","active")
    assert job["reserved_bytes"] == manager.limits.workspace_bytes
    assert manager.validate("alice",upload,session["upload_capability"],retry=True)["state"] == "validating"
    with pytest.raises(WorkerUnconfirmed):
        manager._failed(upload,"VALIDATION_CANCELLED")
    with pytest.raises(UploadError) as caught:
        manager.cleanup_attempt("alice",upload,session["upload_capability"],job["id"])
    assert caught.value.code == "ATTEMPT_PROTECTED"


def test_unreadable_commit_outcome_is_not_reported_as_rollback(integrated_api,tmp_path):
    client,app,owners,session,adapter = setup_upload(integrated_api,tmp_path)
    store,manager = app.state.store,app.state.manager
    manager.runner = InlineRunner()
    original = store.db
    class LostReadback(FaultDB):
        applied = False
        def commit(self):
            self.db.commit()
            if self.matches:
                self.applied = True
                raise sqlite3.OperationalError("injected lost acknowledgement")
        def execute(self,sql,*args):
            if self.applied and sql.startswith("SELECT * FROM validation_datasets"):
                raise sqlite3.OperationalError("injected unavailable readback")
            return super().execute(sql,*args)
    store.db = LostReadback(original,target="SET state='ready'")
    try:
        response = client.post(f"/api/v1/uploads/{session['upload_id']}/validate",headers=auth(owners,session))
    finally:
        store.db = original
    assert response.status_code == 503
    row,job,leases = snapshot(store,session["upload_id"])
    assert row["state"] == "ready" and job["state"] == "published"
    assert job["reserved_bytes"] == 0 and leases == 0


@pytest.mark.parametrize("stage", ["connect","query","close"])
def test_index_error_is_not_authoritative_registry_error(integrated_api,tmp_path,monkeypatch,stage):
    client,app,owners,session,adapter = setup_upload(integrated_api,tmp_path)
    manager = app.state.manager
    manager.runner = InlineRunner()
    original_manifest = manager._manifest
    original_connect = sqlite3.connect
    def fail_index(*args):
        with monkeypatch.context() as patch:
            def fail(*args,**kwargs):
                if stage == "connect":
                    raise sqlite3.DatabaseError("injected bad index")
                db = original_connect(*args,**kwargs)
                class IndexDB:
                    def execute(self,*args):
                        if stage == "query":
                            raise sqlite3.DatabaseError("injected bad index query")
                        return db.execute(*args)
                    def close(self):
                        db.close()
                        if stage == "close":
                            raise sqlite3.DatabaseError("injected bad index cleanup")
                return IndexDB()
            patch.setattr(sqlite3,"connect",fail)
            return original_manifest(*args)
    monkeypatch.setattr(manager,"_manifest",fail_index)
    response = client.post(f"/api/v1/uploads/{session['upload_id']}/validate",headers=auth(owners,session))
    assert response.status_code == 200
    assert response.json()["error_code"] == "INDEX_INTEGRITY_FAILED"
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize("policy", [False,True])
def test_registry_errors_keep_existing_api_classification(integrated_api,tmp_path,policy):
    client,app,owners,session,adapter = setup_upload(integrated_api,tmp_path)
    store,manager = app.state.store,app.state.manager
    manager.runner = InlineRunner()
    failure = sqlite3.OperationalError("injected authoritative registry failure")
    if policy:
        with closing(sqlite3.connect(":memory:")) as probe:
            probe.executescript("CREATE TABLE probe(id); CREATE TRIGGER reject BEFORE INSERT ON probe BEGIN SELECT RAISE(ABORT,'DATASET_PAYLOAD_PINNED'); END;")
            try:
                probe.execute("INSERT INTO probe VALUES (1)")
            except sqlite3.IntegrityError as error:
                failure = error
    original = store.db
    class FailedReadyCommit(FaultDB):
        def commit(self):
            if self.matches:
                raise failure
            self.db.commit()
    store.db = FailedReadyCommit(original,target="SET state='ready'")
    try:
        response = client.post(f"/api/v1/uploads/{session['upload_id']}/validate",headers=auth(owners,session))
    finally:
        store.db = original
    assert response.status_code == (409 if policy else 503)
    assert response.json() == {"error":{"code":"DATASET_PAYLOAD_PINNED" if policy else "REGISTRY_UNAVAILABLE"}}
    assert client.get("/health").status_code == (200 if policy else 503)
    row,job,_ = snapshot(store,session["upload_id"])
    assert row["state"] == "publishing" and job["state"] == "active"
    assert job["reserved_bytes"] == manager.limits.workspace_bytes
