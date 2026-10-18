"""Deterministic regressions for shared registry and analysis-cache locking."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3
import threading

import pytest

from backend.integrated_api import AnalyzeRequest
from storage.uploads import ChunkWriter, UploadStore
from tests.fixtures import make_zip
from tests.test_integrated_api import integrated_api, auth, upload_fixture_via_api


def test_abort_repair_failure_waits_for_other_session_transaction(monkeypatch):
    repair_failed = threading.Event()
    transaction_open = threading.Event()
    guard_attempted = threading.Event()
    release_transaction = threading.Event()
    abort_thread = []

    class ObservedGuard:
        def __init__(self):
            self.lock = threading.RLock()

        def __enter__(self):
            if transaction_open.is_set() and threading.get_ident() in abort_thread:
                guard_attempted.set()
            self.lock.acquire()
            return self

        def __exit__(self, *args):
            self.lock.release()

    store = UploadStore.__new__(UploadStore)
    store.guard = ObservedGuard()
    store.db = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    store.db.execute("CREATE TABLE uploads(id TEXT PRIMARY KEY, state TEXT, error_code TEXT)")
    store.db.executemany("INSERT INTO uploads VALUES (?, 'created', NULL)", [("a",), ("b",)])
    # No filesystem-backed store or writer is needed for this failure path.
    writer = ChunkWriter.__new__(ChunkWriter)
    writer.store, writer.row = store, {"id": "a"}
    writer.lock = threading.RLock()
    writer.finished, writer.closed, writer.replay, writer.scratch = False, False, False, None
    store.active_writers = {"a": writer}

    def broken_repair(upload_id):
        assert upload_id == "a"
        repair_failed.set()
        assert transaction_open.wait(10)
        raise OSError("injected repair failure")

    monkeypatch.setattr(store, "_repair", broken_repair)

    def other_session():
        assert repair_failed.wait(10)
        with store.guard:
            with store.transaction():
                store.db.execute("UPDATE uploads SET state='uploading' WHERE id='b'")
                transaction_open.set()
                assert release_transaction.wait(10)

    def abort():
        abort_thread.append(threading.get_ident())
        writer.abort()

    try:
        with ThreadPoolExecutor(2) as pool:
            other = pool.submit(other_session)
            aborted = pool.submit(abort)
            try:
                assert guard_attempted.wait(10)
                assert not aborted.done()
            finally:
                release_transaction.set()
            other.result(timeout=10)
            aborted.result(timeout=10)
        assert store.db.execute("SELECT state,error_code FROM uploads WHERE id='a'").fetchone() == (
            "failed", "RECOVERY_IO_FAILED")
        assert store.db.execute("SELECT state,error_code FROM uploads WHERE id='b'").fetchone() == (
            "uploading", None)
        assert writer.closed and not writer.finished
        assert store.active_writers == {}
        assert not store.db.in_transaction
    finally:
        store.db.close()


@pytest.mark.parametrize("cache_hit", [False, True])
@pytest.mark.parametrize("reader", ["dashboard", "results"])
def test_cache_reader_waits_for_analysis_cache_write(integrated_api, tmp_path, monkeypatch, cache_hit, reader):
    client, app, owners = integrated_api
    session = upload_fixture_via_api(client, owners, make_zip(tmp_path / "fixture.zip").read_bytes())
    headers = auth(owners, session)
    validated = client.post(f"/api/v1/uploads/{session['upload_id']}/validate", headers=headers)
    assert validated.status_code == 200, validated.text
    assert validated.json()["state"] == "ready"
    dataset = validated.json()["dataset_id"]
    base = f"/api/v1/datasets/{dataset}"
    initial = client.post(base + "/analyze", headers=headers, json={})
    assert initial.status_code == 200, initial.text
    body = {} if cache_hit else {"image_cfg": {}}
    fingerprint = hashlib.sha256(json.dumps(
        AnalyzeRequest.model_validate(body).model_dump(), sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    cache = app.state.cache_dir / f"{dataset}.json"
    writing = threading.Event()
    release_write = threading.Event()
    reader_waiting = threading.Event()
    cache_read = threading.Event()
    real_write, real_read = Path.write_text, Path.read_text
    endpoint = next(route.endpoint for route in app.routes
                    if getattr(route, "path", "") == "/api/v1/datasets/{dataset_id}/analyze")
    analysis_lock = inspect.getclosurevars(endpoint).nonlocals["analysis_lock"]
    real_acquire = analysis_lock.acquire

    async def observed_acquire():
        if writing.is_set():
            reader_waiting.set()
        return await real_acquire()

    def paused_write(path, data, *args, **kwargs):
        if path != cache:
            return real_write(path, data, *args, **kwargs)
        real_write(path, data[:len(data) // 2], *args, **kwargs)
        writing.set()
        assert release_write.wait(10)
        return real_write(path, data, *args, **kwargs)

    def observed_read(path, *args, **kwargs):
        if path == cache and writing.is_set():
            cache_read.set()
            reader_waiting.set()
            assert release_write.is_set(), "reader read partially written cache"
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(analysis_lock, "acquire", observed_acquire)
    monkeypatch.setattr(Path, "write_text", paused_write)
    monkeypatch.setattr(Path, "read_text", observed_read)
    with ThreadPoolExecutor(2) as pool:
        analysis = pool.submit(client.post, base + "/analyze", headers=headers, json=body)
        try:
            assert writing.wait(10)
            if reader == "results":
                reading = pool.submit(client.get, base + "/results", headers=headers)
            else:
                reading = pool.submit(client.post, base + "/dashboard", headers=headers,
                                      json={"page": "overview", "expected_fingerprint": fingerprint})
            assert reader_waiting.wait(10)
            assert not cache_read.is_set()
            assert not reading.done()
            # Upload admission does not acquire the analysis lock.
            unrelated = client.post("/api/v1/uploads", headers={
                "X-Owner-Key": owners["bob"], "Idempotency-Key": "independent-upload-during-analysis",
            }, json={"filename": "other.zip", "size_bytes": 16})
            assert unrelated.status_code == 201, unrelated.text
        finally:
            release_write.set()
        analyzed = analysis.result(timeout=10)
        response = reading.result(timeout=10)
    assert analyzed.status_code == 200, analyzed.text
    assert response.status_code == 200, response.text
    assert cache_read.is_set()
    if reader == "results":
        assert response.json() == analyzed.json()
        assert response.json()["request_fingerprint"] == fingerprint
    else:
        assert response.json()["source"]["request_fingerprint"] == fingerprint
        assert response.json()["dataset_id"] == dataset
        assert response.json()["page"] == "overview"
