"""Cancellation races against isolated API roots, never against live services."""
from concurrent.futures import ThreadPoolExecutor
import io
import sqlite3
import threading

import httpx
import pytest

from datasets.limits import ValidationError
from datasets.manager import DatasetManager
from datasets.runner import WorkerRunner
from datasets.validate import validate_archive
from storage.uploads import UploadStore
from tests.fixtures import make_zip
from tests.test_integrated_api import integrated_api, upload_fixture_via_api, auth
from ui.backend_adapter import BackendClient, BackendError


def setup_upload(fixture, tmp_path):
    client, app, owners = fixture
    data = make_zip(tmp_path / "cancellation.zip").read_bytes()
    session = upload_fixture_via_api(client, owners, data)
    adapter = BackendClient(owner_key=owners["alice"], http_client=client)
    return client, app, owners, session, adapter


def attempt(app, upload_id):
    with app.state.store.guard:
        return dict(app.state.store.db.execute(
            "SELECT * FROM validation_attempts WHERE upload_id=? ORDER BY rowid DESC", (upload_id,),
        ).fetchone())


def assert_retained(app, upload_id, state="failed"):
    row = attempt(app, upload_id)
    assert row["state"] == state
    assert row["reserved_bytes"] == app.state.manager.limits.workspace_bytes
    with app.state.store.guard:
        assert app.state.store.db.execute("SELECT COUNT(*) FROM validation_leases").fetchone()[0] == 0
        assert app.state.store._row(upload_id)["state"] == "uploaded"
    return row


class GatedRunner:
    def __init__(self, *, failure=None, sealed=False):
        self.entered, self.release = threading.Event(), threading.Event()
        self.failure, self.sealed, self.calls = failure, sealed, 0

    def run(self, payload, work, dataset_id, limits, *, cancelled, progress, **kwargs):
        self.calls += 1
        if self.sealed:
            validate_archive(payload, work, dataset_id, limits)
        progress("indexing")
        self.entered.set()
        assert self.release.wait(10), "test gate was not released"
        if self.failure:
            raise ValidationError(self.failure)
        if self.sealed:
            # Deliberately ignore cancellation here: manager must check before publish.
            return
        if cancelled():
            raise ValidationError("VALIDATION_CANCELLED")
        validate_archive(payload, work, dataset_id, limits)


def test_real_isolated_worker_can_be_cancelled_while_running(integrated_api, tmp_path, monkeypatch):
    client, app, owners, session, adapter = setup_upload(integrated_api, tmp_path)
    manager = app.state.manager
    indexing = threading.Event()
    processes = []
    original_set = manager._set
    def set_state(upload, state, code=None):
        original_set(upload, state, code)
        if state == "indexing":
            indexing.set()
    monkeypatch.setattr(manager, "_set", set_state)
    manager.runner = WorkerRunner(module="tests.cancellation_worker", started=processes.append)
    with ThreadPoolExecutor(2) as executor:
        validation = executor.submit(adapter.start_validation, session["upload_id"], session["upload_capability"])
        try:
            assert indexing.wait(10), "test worker never reported indexing"
            assert processes[0].poll() is None
            cancellation = executor.submit(adapter.cancel_validation, session["upload_id"], session["upload_capability"])
            response = cancellation.result(timeout=3)
            assert response["state"] in {"indexing", "cancelled"}
            result = validation.result(timeout=10)
            assert result["state"] == "cancelled"
            assert result["error_code"] == "VALIDATION_CANCELLED"
            assert processes[0].poll() is not None
            assert_retained(app, session["upload_id"])
            assert not list((manager.root / "sealed").iterdir())
        finally:
            # Only a test-owned child may be killed if an assertion fails.
            if processes and processes[0].poll() is None:
                processes[0].kill()
                processes[0].wait(timeout=10)


@pytest.mark.parametrize("failure,sealed", [(None, False), ("VALIDATION_TIMEOUT", False), (None, True)])
def test_cancel_before_completion_retains_reservation_and_retry_is_unique(integrated_api, tmp_path, failure, sealed):
    client, app, owners, session, adapter = setup_upload(integrated_api, tmp_path)
    runner = GatedRunner(failure=failure, sealed=sealed)
    app.state.manager.runner = runner
    upload, capability = session["upload_id"], session["upload_capability"]
    with ThreadPoolExecutor(2) as executor:
        pending = executor.submit(adapter.start_validation, upload, capability)
        try:
            assert runner.entered.wait(5)
            cancellation = executor.submit(adapter.cancel_validation, upload, capability)
            assert cancellation.result(timeout=3)["state"] == "indexing"
            assert_retained(app, upload, "active")
            replay = executor.submit(adapter.start_validation, upload, capability, retry=True)
            with pytest.raises(TimeoutError):
                replay.result(timeout=0.01)
            assert runner.calls == 1
        finally:
            runner.release.set()
        expected = "failed" if failure else "cancelled"
        assert pending.result(timeout=10)["state"] == expected
        assert replay.result(timeout=10)["state"] == expected
        assert adapter.get_dataset_status(upload, capability)["error_code"] == (failure or "VALIDATION_CANCELLED")
    old = assert_retained(app, upload)
    assert not list((app.state.manager.root / "sealed").iterdir())
    assert adapter.cancel_validation(upload, capability)["state"] == expected
    with pytest.raises(BackendError) as caught:
        adapter.start_validation(upload, capability)
    assert caught.value.code == "EXPLICIT_RETRY_REQUIRED"
    app.state.manager.runner = WorkerRunner()
    result = adapter.start_validation(upload, capability, retry=True)
    assert result["state"] == "ready"
    assert adapter.start_validation(upload, capability, retry=True) == result
    with app.state.store.guard:
        rows = [dict(r) for r in app.state.store.db.execute("SELECT * FROM validation_attempts ORDER BY rowid")]
        assert len(rows) == 2
        assert rows[0] == old
        assert rows[1]["state"] == "published" and rows[1]["reserved_bytes"] == 0
        assert app.state.store.db.execute("SELECT COUNT(*) FROM validation_leases").fetchone()[0] == 0


def test_client_timeout_and_lost_response_do_not_duplicate_attempt(integrated_api, tmp_path):
    client, app, owners, session, adapter = setup_upload(integrated_api, tmp_path)
    runner = GatedRunner()
    app.state.manager.runner = runner
    upload, capability = session["upload_id"], session["upload_capability"]
    # This models an HTTP client timing out while the server request remains active.
    with ThreadPoolExecutor(2) as executor:
        pending = executor.submit(adapter.start_validation, upload, capability)
        try:
            assert runner.entered.wait(5)
            with pytest.raises(TimeoutError):
                pending.result(timeout=0.01)
            replay = executor.submit(adapter.start_validation, upload, capability)
            with pytest.raises(TimeoutError):
                replay.result(timeout=0.01)
            assert runner.calls == 1
        finally:
            runner.release.set()
        result = pending.result(timeout=10)
        assert replay.result(timeout=10) == result
    assert result["state"] == "ready"
    real_post = client.post
    lost = False
    def post(*args, **kwargs):
        nonlocal lost
        response = real_post(*args, **kwargs)
        if not lost:
            lost = True
            raise httpx.ReadTimeout("simulated lost response")
        return response
    client.post = post
    with pytest.raises(BackendError) as caught:
        adapter.start_validation(upload, capability)
    assert caught.value.code == "BACKEND_UNAVAILABLE"
    assert adapter.start_validation(upload, capability) == result
    assert runner.calls == 1
    assert attempt(app, upload)["state"] == "published"
    with app.state.store.guard:
        assert app.state.store.db.execute("SELECT COUNT(*) FROM validation_attempts").fetchone()[0] == 1


def test_publication_wins_cancel_keeps_ready_and_reader_lease(integrated_api, tmp_path):
    client, app, owners, session, adapter = setup_upload(integrated_api, tmp_path)
    manager = app.state.manager
    at_publish, release = threading.Event(), threading.Event()
    def fault(point):
        if point == "publishing_commit":
            at_publish.set()
            assert release.wait(10)
    manager.fault = fault
    upload, capability = session["upload_id"], session["upload_capability"]
    with ThreadPoolExecutor(2) as executor:
        pending = executor.submit(adapter.start_validation, upload, capability)
        try:
            assert at_publish.wait(10)
            cancel = executor.submit(adapter.cancel_validation, upload, capability)
        finally:
            release.set()
        result = pending.result(timeout=10)
        assert result["state"] == "ready"
        with pytest.raises(BackendError) as caught:
            cancel.result(timeout=10)
        assert caught.value.code == "NO_CANCELLABLE_VALIDATION" and caught.value.status_code == 409
    with manager.reader("alice", upload, capability):
        assert app.state.store.db.execute("SELECT COUNT(*) FROM validation_leases").fetchone()[0] == 1
        with pytest.raises(BackendError):
            adapter.cancel_validation(upload, capability)
        assert app.state.store.db.execute("SELECT COUNT(*) FROM validation_leases").fetchone()[0] == 1
    assert app.state.store.db.execute("SELECT COUNT(*) FROM validation_leases").fetchone()[0] == 0


@pytest.mark.parametrize("phase", ["validating", "indexing", "publishing"])
def test_restart_preserves_cancel_intent_and_reservation(integrated_api, tmp_path, phase):
    client, app, owners, session, adapter = setup_upload(integrated_api, tmp_path)
    manager, store = app.state.manager, app.state.store
    import uuid
    upload = session["upload_id"]
    with store.guard, store.transaction():
        store.db.execute("INSERT INTO validation_datasets (upload_id,dataset_id,attempt_id,state,cancel_requested) VALUES (?,?,?,?,1)",
                         (upload, str(uuid.uuid4()), "11111111-1111-4111-8111-111111111111", phase))
        store.db.execute("INSERT INTO validation_attempts VALUES (?,?,'active',?)",
                         ("11111111-1111-4111-8111-111111111111", upload, manager.limits.workspace_bytes))
    # Test-owned root only: emulate reopening after process exit, with no worker running.
    cfg = store.config
    store.close()
    reopened = UploadStore(cfg)
    try:
        recovered = DatasetManager(reopened)
        status = recovered.status("alice", upload, session["upload_capability"])
        assert status["state"] == "cancelled" and status["error_code"] == "VALIDATION_CANCELLED"
        row = reopened.db.execute("SELECT * FROM validation_attempts").fetchone()
        assert row["state"] == "failed" and row["reserved_bytes"] == manager.limits.workspace_bytes
    finally:
        reopened.close()


def test_terminal_transition_is_atomic_on_registry_error(integrated_api, tmp_path):
    client, app, owners, session, adapter = setup_upload(integrated_api, tmp_path)
    runner = GatedRunner()
    app.state.manager.runner = runner
    upload = session["upload_id"]
    with ThreadPoolExecutor(1) as executor:
        pending = executor.submit(adapter.start_validation, upload, session["upload_capability"])
        try:
            assert runner.entered.wait(5)
            original = app.state.store.db
            class FailAttemptUpdate:
                def __getattr__(self, name):
                    return getattr(original, name)
                def execute(self, sql, *args):
                    if sql.startswith("UPDATE validation_attempts SET state='failed'"):
                        raise sqlite3.OperationalError("injected registry failure")
                    return original.execute(sql, *args)
            with app.state.store.guard:
                app.state.store.db = FailAttemptUpdate()
                try:
                    with pytest.raises(sqlite3.OperationalError):
                        app.state.manager._failed(upload, "VALIDATION_CANCELLED")
                finally:
                    app.state.store.db = original
                assert app.state.manager._row(upload)["state"] == "indexing"
                assert_retained(app, upload, "active")
            adapter.cancel_validation(upload, session["upload_capability"])
        finally:
            runner.release.set()
        assert pending.result(timeout=10)["state"] == "cancelled"


def test_concurrent_validation_keeps_global_admission_and_cleanup_guards(integrated_api, tmp_path):
    client, app, owners, session, adapter = setup_upload(integrated_api, tmp_path)
    other = BackendClient(owner_key=owners["bob"], http_client=client)
    data = make_zip(tmp_path / "other.zip").read_bytes()
    second = other.stream_upload(io.BytesIO(data), "other.zip", len(data))
    runner = GatedRunner()
    app.state.manager.runner = runner
    upload, capability = session["upload_id"], session["upload_capability"]
    with ThreadPoolExecutor(4) as executor:
        pending = executor.submit(adapter.start_validation, upload, capability)
        try:
            assert runner.entered.wait(5)
            replays = [executor.submit(adapter.start_validation, upload, capability) for _ in range(3)]
            for future in replays:
                with pytest.raises(TimeoutError):
                    future.result(timeout=0.01)
            with pytest.raises(BackendError) as caught:
                other.start_validation(second["upload_id"], second["upload_capability"])
            assert caught.value.code == "VALIDATION_OR_UPLOAD_BUSY"
            with pytest.raises(BackendError) as admission:
                other.create_upload("blocked.zip", 1)
            assert admission.value.code == "VALIDATION_WORKSPACE_RESERVED"
            active = assert_retained(app, upload, "active")
            url = f"/api/v1/uploads/{upload}/dataset/attempts/{active['id']}"
            protected = client.delete(url, headers=auth(owners, session))
            assert protected.status_code == 409 and protected.json()["error"]["code"] == "ATTEMPT_PROTECTED"
            assert client.get("/health").status_code == 200
            adapter.cancel_validation(upload, capability)
        finally:
            runner.release.set()
        assert pending.result(timeout=10)["state"] == "cancelled"
        assert all(f.result(timeout=10)["state"] == "cancelled" for f in replays)
    assert runner.calls == 1
    assert assert_retained(app, upload) == {**active, "state": "failed"}
    # Supported cleanup only, restricted to the failed attempt in this test root.
    response = client.delete(url, headers=auth(owners, session))
    assert response.status_code == 200 and response.json() == {"cleaned": True}
    assert attempt(app, upload)["reserved_bytes"] == 0
    assert attempt(app, upload)["state"] == "cleaned"
    assert other.get_dataset_status(second["upload_id"], second["upload_capability"])["state"] == "not_requested"


def test_lost_cancel_response_can_be_retried_without_early_release(integrated_api, tmp_path):
    client, app, owners, session, adapter = setup_upload(integrated_api, tmp_path)
    runner = GatedRunner()
    app.state.manager.runner = runner
    upload, capability = session["upload_id"], session["upload_capability"]
    original_post = client.post
    lost = False
    def post(url, **kwargs):
        nonlocal lost
        response = original_post(url, **kwargs)
        if str(url).endswith("/dataset/cancel") and not lost:
            lost = True
            raise httpx.ReadError("simulated lost cancellation acknowledgement")
        return response
    client.post = post
    with ThreadPoolExecutor(2) as executor:
        pending = executor.submit(adapter.start_validation, upload, capability)
        try:
            assert runner.entered.wait(5)
            first = executor.submit(adapter.cancel_validation, upload, capability)
            with pytest.raises(BackendError):
                first.result(timeout=3)
            assert adapter.cancel_validation(upload, capability)["state"] == "indexing"
            assert_retained(app, upload, "active")
        finally:
            runner.release.set()
        assert pending.result(timeout=10)["state"] == "cancelled"
    assert runner.calls == 1
    assert_retained(app, upload)


@pytest.mark.parametrize("code", ["VALIDATION_TIMEOUT", "VALIDATION_FAILED"])
def test_worker_error_without_cancel_requires_explicit_retry(integrated_api, tmp_path, code):
    client, app, owners, session, adapter = setup_upload(integrated_api, tmp_path)
    runner = GatedRunner(failure=code)
    runner.release.set()
    app.state.manager.runner = runner
    upload, capability = session["upload_id"], session["upload_capability"]
    result = adapter.start_validation(upload, capability)
    assert result["state"] == "failed" and result["error_code"] == code
    old = assert_retained(app, upload)
    with pytest.raises(BackendError) as caught:
        adapter.start_validation(upload, capability)
    assert caught.value.code == "EXPLICIT_RETRY_REQUIRED"
    assert runner.calls == 1
    assert adapter.start_validation(upload, capability, retry=True)["state"] == "failed"
    assert runner.calls == 2
    with app.state.store.guard:
        rows = [dict(r) for r in app.state.store.db.execute("SELECT * FROM validation_attempts ORDER BY rowid")]
        assert len(rows) == 2 and rows[0] == old
        assert all(r["state"] == "failed" and r["reserved_bytes"] == old["reserved_bytes"] for r in rows)
    assert not list((app.state.manager.root / "sealed").iterdir())


def test_unrelated_analysis_does_not_block_validation_cancel(integrated_api, tmp_path, monkeypatch):
    client, app, owners, first, adapter = setup_upload(integrated_api, tmp_path)
    ready = adapter.start_validation(first["upload_id"], first["upload_capability"])
    data = make_zip(tmp_path / "second.zip").read_bytes()
    second = adapter.stream_upload(io.BytesIO(data), "second.zip", len(data))
    runner = GatedRunner()
    app.state.manager.runner = runner
    import backend.integrated_api as api
    original_pipeline = api.execute_pipeline
    entered, release = threading.Event(), threading.Event()
    def pipeline(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original_pipeline(*args, **kwargs)
    monkeypatch.setattr(api, "execute_pipeline", pipeline)
    with ThreadPoolExecutor(3) as executor:
        validation = executor.submit(adapter.start_validation, second["upload_id"], second["upload_capability"])
        try:
            assert runner.entered.wait(5)
            analysis = executor.submit(adapter.trigger_analysis, ready["dataset_id"], first["upload_capability"])
            assert entered.wait(5)
            cancel = executor.submit(adapter.cancel_validation, second["upload_id"], second["upload_capability"])
            assert cancel.result(timeout=3)["state"] == "indexing"
        finally:
            runner.release.set()
            release.set()
        assert validation.result(timeout=10)["state"] == "cancelled"
        assert analysis.result(timeout=10)["dataset_id"] == ready["dataset_id"]
