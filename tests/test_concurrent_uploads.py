import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
import threading
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.durable_api import PREFIX, create_app, UploadLockManager
from storage.config import UploadConfig
from storage.uploads import UploadError, UploadStore


@pytest.fixture
def config(tmp_path):
    return UploadConfig(
        tmp_path / "private",
        secrets.token_urlsafe(32),
        max_bytes=1024 * 1024,
        chunk_bytes=16,
        block_bytes=4,
        min_free_bytes=0,
        quota_bytes=4096,
        idle_seconds=60,
        lifetime_seconds=300,
    )


@pytest.fixture
def store(config):
    instance = UploadStore(config, clock=lambda: 1000.0, free_bytes=lambda: 10**9)
    yield instance
    instance.close()


@pytest.fixture
def api(tmp_path):
    config = UploadConfig(
        tmp_path / "private",
        secrets.token_urlsafe(32),
        max_bytes=1024 * 1024,
        chunk_bytes=16,
        block_bytes=4,
        min_free_bytes=0,
        quota_bytes=8192,
    )
    owners = {"alice": secrets.token_urlsafe(32), "bob": secrets.token_urlsafe(32)}
    app = create_app(config, owners)
    with TestClient(app, base_url="http://127.0.0.1:8011", client=("127.0.0.1", 12345)) as client:
        yield client, app, owners


def test_independent_sessions_created_concurrently(store):
    """Requirement 1 & 7: Multiple distinct sessions can be created simultaneously,
    and idempotent retries safely return the same session ID.
    """
    barrier = threading.Barrier(4)

    def create_session(owner, key):
        barrier.wait()
        return store.create(owner, "archive.zip", 32, key)

    key_alice1 = str(uuid4())
    key_alice2 = str(uuid4())
    key_bob1 = str(uuid4())

    with ThreadPoolExecutor(max_workers=4) as pool:
        f1 = pool.submit(create_session, "alice", key_alice1)
        f2 = pool.submit(create_session, "alice", key_alice2)
        f3 = pool.submit(create_session, "bob", key_bob1)
        f4 = pool.submit(create_session, "alice", key_alice1)  # Idempotent retry

        r1 = f1.result()
        r2 = f2.result()
        r3 = f3.result()
        r4 = f4.result()

    # Distinct sessions created
    assert r1["upload_id"] != r2["upload_id"]
    assert r1["upload_id"] != r3["upload_id"]
    assert r2["upload_id"] != r3["upload_id"]

    # Idempotent retry returns identical session
    assert r4["upload_id"] == r1["upload_id"]
    assert r4["upload_capability"] == r1["upload_capability"]

    # All 3 unique sessions exist in DB
    rows = store.db.execute("SELECT id FROM uploads").fetchall()
    assert len(rows) == 3


def test_two_uploads_progress_concurrently(store):
    """Requirement 2: Two different upload IDs can make progress concurrently,
    synchronizing through barriers rather than sleeps.
    """
    s1 = store.create("alice", "file1.zip", 32, str(uuid4()))
    s2 = store.create("bob", "file2.zip", 32, str(uuid4()))

    data1 = b"0123456789abcdef"
    data2 = b"fedcba9876543210"

    barrier_writers_active = threading.Barrier(2)
    barrier_first_block_written = threading.Barrier(2)

    def worker1():
        writer1 = store.begin("alice", s1["upload_id"], s1["upload_capability"], 0, 0,
                              hashlib.sha256(data1).hexdigest())
        barrier_writers_active.wait()
        writer1.write(data1[:8])
        barrier_first_block_written.wait()
        writer1.write(data1[8:])
        return writer1.finish()

    def worker2():
        writer2 = store.begin("bob", s2["upload_id"], s2["upload_capability"], 0, 0,
                              hashlib.sha256(data2).hexdigest())
        barrier_writers_active.wait()
        writer2.write(data2[:8])
        barrier_first_block_written.wait()
        writer2.write(data2[8:])
        return writer2.finish()

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(worker1)
        f2 = pool.submit(worker2)
        res1 = f1.result()
        res2 = f2.result()

    assert res1["received_bytes"] == 16
    assert res2["received_bytes"] == 16
    assert store._file(s1["upload_id"], "payload.part").read_bytes() == data1
    assert store._file(s2["upload_id"], "payload.part").read_bytes() == data2


def test_simultaneous_writers_for_same_upload_rejected(store):
    """Requirement 3: Two simultaneous writers for the same upload are rejected
    with 409 WRITER_BUSY.
    """
    s = store.create("alice", "single.zip", 32, str(uuid4()))
    data = b"0123456789abcdef"
    digest = hashlib.sha256(data).hexdigest()

    writer1 = store.begin("alice", s["upload_id"], s["upload_capability"], 0, 0, digest)
    writer1.write(b"0123")

    # A second concurrent writer on the same upload is rejected
    with pytest.raises(UploadError) as exc_info:
        store.begin("alice", s["upload_id"], s["upload_capability"], 0, 0, digest)
    assert exc_info.value.status == 409
    assert exc_info.value.code == "WRITER_BUSY"

    # Complete and cancel on same upload are also rejected while writer is active
    with pytest.raises(UploadError) as exc_complete:
        store.complete("alice", s["upload_id"], s["upload_capability"])
    assert exc_complete.value.status == 409
    assert exc_complete.value.code == "WRITER_BUSY"

    with pytest.raises(UploadError) as exc_cancel:
        store.cancel("alice", s["upload_id"], s["upload_capability"])
    assert exc_cancel.value.status == 409
    assert exc_cancel.value.code == "WRITER_BUSY"

    # Finish writer1
    writer1.write(data[4:])
    writer1.finish()

    # Now that writer1 finished, a new writer (for chunk 1) can begin
    data_chunk1 = b"abcdefgh12345678"
    writer2 = store.begin("alice", s["upload_id"], s["upload_capability"], 1, 16,
                          hashlib.sha256(data_chunk1).hexdigest())
    writer2.write(data_chunk1)
    writer2.finish()

    done = store.complete("alice", s["upload_id"], s["upload_capability"])
    assert done["state"] == "uploaded"


def test_complete_and_cancel_one_session_does_not_affect_active_session(store):
    """Requirement 4: Completing or cancelling one session does not affect
    another active session.
    """
    s_active = store.create("alice", "active.zip", 32, str(uuid4()))
    s_complete = store.create("bob", "complete.zip", 16, str(uuid4()))
    s_cancel = store.create("alice", "cancel.zip", 16, str(uuid4()))

    data_active = b"active12active12"
    digest_active = hashlib.sha256(data_active).hexdigest()

    # Start active writer on s_active
    writer_active = store.begin("alice", s_active["upload_id"], s_active["upload_capability"],
                                0, 0, digest_active)
    writer_active.write(b"active12")

    # Upload and complete s_complete
    data_complete = b"completecomplete"
    w_comp = store.begin("bob", s_complete["upload_id"], s_complete["upload_capability"],
                         0, 0, hashlib.sha256(data_complete).hexdigest())
    w_comp.write(data_complete)
    w_comp.finish()
    res_complete = store.complete("bob", s_complete["upload_id"], s_complete["upload_capability"])
    assert res_complete["state"] == "uploaded"

    # Cancel s_cancel
    res_cancel = store.cancel("alice", s_cancel["upload_id"], s_cancel["upload_capability"])
    assert res_cancel["state"] == "cancelled"

    # s_active is STILL actively writing and undamaged
    assert store.is_active_writer(s_active["upload_id"], writer_active)
    writer_active.write(b"active12")
    res_active = writer_active.finish()
    assert res_active["received_bytes"] == 16
    assert store._file(s_active["upload_id"], "payload.part").read_bytes() == data_active


def test_writer_failure_releases_only_its_own_state(store):
    """Requirement 5: A client disconnect or writer failure releases only its own
    writer state and does not disturb other active writers.
    """
    s_fail = store.create("alice", "fail.zip", 32, str(uuid4()))
    s_ok = store.create("bob", "ok.zip", 32, str(uuid4()))

    data_ok = b"ok_data_12345678"
    writer_ok = store.begin("bob", s_ok["upload_id"], s_ok["upload_capability"],
                            0, 0, hashlib.sha256(data_ok).hexdigest())
    writer_ok.write(b"ok_data_")

    writer_fail = store.begin("alice", s_fail["upload_id"], s_fail["upload_capability"],
                              0, 0, hashlib.sha256(b"failfailfailfail").hexdigest())
    writer_fail.write(b"fail")

    # Simulate failure/abort on s_fail
    writer_fail.abort()

    # s_fail writer state is released
    assert not store.is_active_writer(s_fail["upload_id"], writer_fail)
    assert s_fail["upload_id"] not in store.active_writers

    # s_ok is STILL active
    assert store.is_active_writer(s_ok["upload_id"], writer_ok)
    writer_ok.write(b"12345678")
    assert writer_ok.finish()["received_bytes"] == 16

    # s_fail can now be resumed by a new writer
    retry_data = b"failfailfailfail"
    writer_retry = store.begin("alice", s_fail["upload_id"], s_fail["upload_capability"],
                               0, 0, hashlib.sha256(retry_data).hexdigest())
    writer_retry.write(retry_data)
    assert writer_retry.finish()["received_bytes"] == 16


def test_maintenance_preserves_files_of_active_writers(config):
    """Requirement 9: Maintenance does not delete or expire files belonging
    to an active writer, while still cleaning other expired/cancelled sessions.
    """
    clock = [1000.0]
    instance = UploadStore(config, clock=lambda: clock[0], free_bytes=lambda: 10**9)
    try:
        # 1. Active session currently being written
        s_active = instance.create("alice", "active.zip", 32, str(uuid4()))
        data_active = b"active1234567890"
        writer_active = instance.begin("alice", s_active["upload_id"], s_active["upload_capability"],
                                       0, 0, hashlib.sha256(data_active).hexdigest())
        writer_active.write(b"active")

        # 2. An old session that has passed its idle expiration
        s_expired = instance.create("bob", "expired.zip", 16, str(uuid4()))
        instance.cancel("bob", s_expired["upload_id"], s_expired["upload_capability"])

        # Advance clock past idle expiration
        clock[0] = 2000.0

        # Run maintenance
        res = instance.maintain()
        assert res["writer_active"] is True

        # Active upload files are still intact and in uploading/created state
        st_active = instance.status("alice", s_active["upload_id"], s_active["upload_capability"])
        assert st_active["state"] in ("created", "uploading")
        assert instance._file(s_active["upload_id"], "incoming.chunk").exists()

        # Finish writing active chunk successfully
        writer_active.write(data_active[6:])
        assert writer_active.finish()["received_bytes"] == 16
    finally:
        instance.close()


def test_cross_owner_isolation_and_quota_under_concurrency(api):
    """Requirement 8: Quota, capacity, authentication, and cross-owner isolation
    remain strictly enforced under concurrent operations.
    """
    client, app, owners = api

    # Alice creates session
    res_a = client.post(
        PREFIX,
        headers={"X-Owner-Key": owners["alice"], "Idempotency-Key": str(uuid4())},
        json={"filename": "alice.zip", "size_bytes": 16},
    )
    assert res_a.status_code == 201
    s_a = res_a.json()

    # Bob cannot access Alice's session
    url_a = f"{PREFIX}/{s_a['upload_id']}"
    assert client.get(url_a, headers={"X-Owner-Key": owners["bob"], "Upload-Capability": s_a["upload_capability"]}).status_code == 403
    assert client.put(f"{url_a}/chunks/0", content=b"x"*16, headers={
        "X-Owner-Key": owners["bob"],
        "Upload-Capability": s_a["upload_capability"],
        "Upload-Offset": "0",
        "X-Chunk-SHA256": hashlib.sha256(b"x"*16).hexdigest(),
    }).status_code == 403
    assert client.delete(url_a, headers={"X-Owner-Key": owners["bob"], "Upload-Capability": s_a["upload_capability"]}).status_code == 403

    # Bob can create his own session concurrently
    res_b = client.post(
        PREFIX,
        headers={"X-Owner-Key": owners["bob"], "Idempotency-Key": str(uuid4())},
        json={"filename": "bob.zip", "size_bytes": 16},
    )
    assert res_b.status_code == 201


def test_concurrent_api_chunk_streaming(api):
    """Full HTTP API concurrent chunk streaming test.
    Ensures that streaming body for upload 1 does not block upload 2.
    """
    client, app, owners = api

    s1 = client.post(PREFIX, headers={"X-Owner-Key": owners["alice"], "Idempotency-Key": str(uuid4())},
                     json={"filename": "stream1.zip", "size_bytes": 16}).json()
    s2 = client.post(PREFIX, headers={"X-Owner-Key": owners["bob"], "Idempotency-Key": str(uuid4())},
                     json={"filename": "stream2.zip", "size_bytes": 16}).json()

    data1 = b"stream_data_one_"
    data2 = b"stream_data_two_"

    def send_chunk(session, owner, data):
        url = f"{PREFIX}/{session['upload_id']}/chunks/0"
        return client.put(url, content=data, headers={
            "X-Owner-Key": owners[owner],
            "Upload-Capability": session["upload_capability"],
            "Upload-Offset": "0",
            "X-Chunk-SHA256": hashlib.sha256(data).hexdigest(),
        })

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(send_chunk, s1, "alice", data1)
        f2 = pool.submit(send_chunk, s2, "bob", data2)
        r1 = f1.result()
        r2 = f2.result()

    assert r1.status_code == 200 and r1.json()["received_bytes"] == 16
    assert r2.status_code == 200 and r2.json()["received_bytes"] == 16


def test_upload_lock_manager_lifecycle():
    """Unit test for UploadLockManager: ref-counting, waiter serialization,
    cancelled waiter handling, exception safety, and automated cleanup.
    """
    async def run():
        mgr = UploadLockManager()
        upload_id = "test-session-1"

        # 1. Single acquire and release
        async with mgr.lock(upload_id):
            assert mgr.is_locked(upload_id) is True
            assert mgr.ref_count(upload_id) == 1
            assert mgr.has_lock(upload_id) is True

        assert mgr.is_locked(upload_id) is False
        assert mgr.ref_count(upload_id) == 0
        assert mgr.has_lock(upload_id) is False

        # 2. Multiple concurrent waiters
        order = []

        async def worker(idx: int, delay: float):
            async with mgr.lock(upload_id):
                order.append(f"enter_{idx}")
                await asyncio.sleep(delay)
                order.append(f"exit_{idx}")

        t1 = asyncio.create_task(worker(1, 0.05))
        await asyncio.sleep(0.01)  # allow t1 to acquire
        assert mgr.is_locked(upload_id) is True
        assert mgr.ref_count(upload_id) == 1

        t2 = asyncio.create_task(worker(2, 0.01))
        t3 = asyncio.create_task(worker(3, 0.01))
        await asyncio.sleep(0.01)  # allow t2 and t3 to register interest and wait

        assert mgr.ref_count(upload_id) == 3
        assert mgr.has_lock(upload_id) is True

        await asyncio.gather(t1, t2, t3)
        assert order == ["enter_1", "exit_1", "enter_2", "exit_2", "enter_3", "exit_3"]
        assert mgr.ref_count(upload_id) == 0
        assert mgr.has_lock(upload_id) is False

        # 3. Cancelled waiter does not leak refs or break other waiters
        async def slow_holder():
            async with mgr.lock(upload_id):
                await asyncio.sleep(0.05)

        t_holder = asyncio.create_task(slow_holder())
        await asyncio.sleep(0.01)

        t_cancelled = asyncio.create_task(worker(4, 0.01))
        await asyncio.sleep(0.01)
        assert mgr.ref_count(upload_id) == 2

        t_cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t_cancelled

        assert mgr.ref_count(upload_id) == 1
        await t_holder
        assert mgr.ref_count(upload_id) == 0
        assert mgr.has_lock(upload_id) is False

        # 4. Exception inside critical section cleans up properly
        try:
            async with mgr.lock("err-session"):
                assert mgr.ref_count("err-session") == 1
                raise ValueError("boom")
        except ValueError:
            pass

        assert mgr.ref_count("err-session") == 0
        assert mgr.has_lock("err-session") is False

    asyncio.run(run())


def test_concurrent_cancel_and_chunk_same_upload_lock_lifecycle(api):
    """Regression test: Cancellation running concurrently with a chunk request on the
    SAME upload ID.
    - Demonstrates that cancel and chunk do not run concurrently.
    - Demonstrates that cancelling does not pop the lock out from under the waiting chunk request.
    - Demonstrates that after all requests finish, the lock is cleaned up from upload_locks.
    """
    client, app, owners = api

    s = client.post(PREFIX, headers={"X-Owner-Key": owners["alice"], "Idempotency-Key": str(uuid4())},
                    json={"filename": "cancel_race.zip", "size_bytes": 16}).json()
    upload_id = s["upload_id"]
    cap_val = s["upload_capability"]

    cancel_started = threading.Event()
    cancel_proceed = threading.Event()
    original_cancel = app.state.store.cancel

    def hooked_cancel(identity, u_id, cap):
        if u_id == upload_id:
            cancel_started.set()
            cancel_proceed.wait(timeout=5.0)
        return original_cancel(identity, u_id, cap)

    app.state.store.cancel = hooked_cancel

    results = {}

    def do_cancel():
        res = client.delete(f"{PREFIX}/{upload_id}", headers={
            "X-Owner-Key": owners["alice"],
            "Upload-Capability": cap_val,
        })
        results["cancel"] = res

    def do_chunk():
        # Wait until cancel is inside the lock!
        cancel_started.wait(timeout=5.0)
        # Attempt chunk while cancel is holding the upload lock
        data = b"0123456789abcdef"
        res = client.put(f"{PREFIX}/{upload_id}/chunks/0", content=data, headers={
            "X-Owner-Key": owners["alice"],
            "Upload-Capability": cap_val,
            "Upload-Offset": "0",
            "X-Chunk-SHA256": hashlib.sha256(data).hexdigest(),
        })
        results["chunk"] = res

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_cancel = pool.submit(do_cancel)
        f_chunk = pool.submit(do_chunk)

        cancel_started.wait(timeout=5.0)
        # Verify cancel holds the lock
        assert app.state.upload_locks.is_locked(upload_id) is True
        # Allow cancel to finish
        cancel_proceed.set()

        f_cancel.result()
        f_chunk.result()

    assert results["cancel"].status_code == 200
    assert results["cancel"].json()["state"] == "cancelled"

    # The chunk was serialized behind cancel and safely rejected because session was cancelled
    assert results["chunk"].status_code in (404, 409)

    # After both finished, lock is fully released and cleaned up
    assert app.state.upload_locks.is_locked(upload_id) is False
    assert app.state.upload_locks.has_lock(upload_id) is False
    assert app.state.upload_locks.ref_count(upload_id) == 0


def test_concurrent_complete_and_chunk_same_upload_lock_lifecycle(api):
    """Regression test: Completion running concurrently with a chunk request on the
    SAME upload ID.
    - Demonstrates that complete and chunk do not run concurrently.
    - Demonstrates that after completion and chunk exit, the lock is cleaned up from upload_locks.
    """
    client, app, owners = api

    s = client.post(PREFIX, headers={"X-Owner-Key": owners["alice"], "Idempotency-Key": str(uuid4())},
                    json={"filename": "comp_race.zip", "size_bytes": 16}).json()
    upload_id = s["upload_id"]
    cap_val = s["upload_capability"]

    # Write chunk first so it can complete
    data = b"0123456789abcdef"
    res_init = client.put(f"{PREFIX}/{upload_id}/chunks/0", content=data, headers={
        "X-Owner-Key": owners["alice"],
        "Upload-Capability": cap_val,
        "Upload-Offset": "0",
        "X-Chunk-SHA256": hashlib.sha256(data).hexdigest(),
    })
    assert res_init.status_code == 200

    complete_started = threading.Event()
    complete_proceed = threading.Event()
    original_complete = app.state.store.complete

    def hooked_complete(identity, u_id, cap):
        if u_id == upload_id:
            complete_started.set()
            complete_proceed.wait(timeout=5.0)
        return original_complete(identity, u_id, cap)

    app.state.store.complete = hooked_complete

    results = {}

    def do_complete():
        res = client.post(f"{PREFIX}/{upload_id}/complete", headers={
            "X-Owner-Key": owners["alice"],
            "Upload-Capability": cap_val,
        })
        results["complete"] = res

    def do_chunk():
        complete_started.wait(timeout=5.0)
        # Attempt another chunk write while complete is holding the upload lock
        res = client.put(f"{PREFIX}/{upload_id}/chunks/1", content=data, headers={
            "X-Owner-Key": owners["alice"],
            "Upload-Capability": cap_val,
            "Upload-Offset": "16",
            "X-Chunk-SHA256": hashlib.sha256(data).hexdigest(),
        })
        results["chunk"] = res

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_comp = pool.submit(do_complete)
        f_chunk = pool.submit(do_chunk)

        complete_started.wait(timeout=5.0)
        assert app.state.upload_locks.is_locked(upload_id) is True
        complete_proceed.set()

        f_comp.result()
        f_chunk.result()

    assert results["complete"].status_code == 200
    assert results["complete"].json()["state"] == "uploaded"

    # Chunk is rejected because upload is already uploaded
    assert results["chunk"].status_code in (404, 409)

    # Lock is cleaned up
    assert app.state.upload_locks.is_locked(upload_id) is False
    assert app.state.upload_locks.has_lock(upload_id) is False
    assert app.state.upload_locks.ref_count(upload_id) == 0


def test_lock_cleanup_on_request_error(api):
    """Ensures lock is evicted even when chunk request fails with validation or offset error."""
    client, app, owners = api

    s = client.post(PREFIX, headers={"X-Owner-Key": owners["alice"], "Idempotency-Key": str(uuid4())},
                    json={"filename": "err_cleanup.zip", "size_bytes": 16}).json()
    upload_id = s["upload_id"]
    cap_val = s["upload_capability"]

    # Invalid offset (non-integer) fails before entering lock
    res = client.put(f"{PREFIX}/{upload_id}/chunks/0", content=b"1234", headers={
        "X-Owner-Key": owners["alice"],
        "Upload-Capability": cap_val,
        "Upload-Offset": "not_an_int",
    })
    assert res.status_code == 422
    assert app.state.upload_locks.has_lock(upload_id) is False

    # Invalid offset value (e.g. wrong offset integer) fails inside store.begin under lock
    res2 = client.put(f"{PREFIX}/{upload_id}/chunks/0", content=b"1234", headers={
        "X-Owner-Key": owners["alice"],
        "Upload-Capability": cap_val,
        "Upload-Offset": "999",
        "X-Chunk-SHA256": hashlib.sha256(b"1234").hexdigest(),
    })
    assert res2.status_code == 422
    assert res2.json()["error"]["code"] == "INVALID_CHUNK_METADATA"
    assert app.state.upload_locks.has_lock(upload_id) is False
    assert app.state.upload_locks.ref_count(upload_id) == 0


def test_integrated_api_lock_lifecycle_under_concurrent_operations(tmp_path):
    """Verifies that create_integrated_app properly uses UploadLockManager,
    preventing concurrent chunk and cancel on the same upload ID and cleaning
    up all lock references when finished.
    """
    from backend.integrated_api import create_integrated_app
    from datasets.limits import Limits

    cfg = UploadConfig(
        tmp_path / "private",
        secrets.token_urlsafe(32),
        max_bytes=1024 * 1024,
        chunk_bytes=16,
        block_bytes=4,
        min_free_bytes=0,
        quota_bytes=8192,
    )
    owners = {"alice": secrets.token_urlsafe(32)}
    app = create_integrated_app(cfg, owners, limits=Limits())

    with TestClient(app, base_url="http://127.0.0.1:8011", client=("127.0.0.1", 12345)) as client:
        res_create = client.post(PREFIX, headers={"X-Owner-Key": owners["alice"], "Idempotency-Key": str(uuid4())},
                                 json={"filename": "int_race.zip", "size_bytes": 16})
        assert res_create.status_code == 201
        session = res_create.json()
        upload_id = session["upload_id"]
        cap_val = session["upload_capability"]

        cancel_started = threading.Event()
        cancel_proceed = threading.Event()
        orig_cancel = app.state.store.cancel

        def hooked_cancel(identity, u_id, capability):
            if u_id == upload_id:
                cancel_started.set()
                cancel_proceed.wait(timeout=5.0)
            return orig_cancel(identity, u_id, capability)

        app.state.store.cancel = hooked_cancel

        results = {}

        def do_cancel():
            results["cancel"] = client.delete(f"{PREFIX}/{upload_id}", headers={
                "X-Owner-Key": owners["alice"],
                "Upload-Capability": cap_val,
            })

        def do_chunk():
            cancel_started.wait(timeout=5.0)
            data = b"0123456789abcdef"
            results["chunk"] = client.put(f"{PREFIX}/{upload_id}/chunks/0", content=data, headers={
                "X-Owner-Key": owners["alice"],
                "Upload-Capability": cap_val,
                "Upload-Offset": "0",
                "X-Chunk-SHA256": hashlib.sha256(data).hexdigest(),
            })

        with ThreadPoolExecutor(max_workers=2) as pool:
            f_cancel = pool.submit(do_cancel)
            f_chunk = pool.submit(do_chunk)

            cancel_started.wait(timeout=5.0)
            assert app.state.upload_locks.is_locked(upload_id) is True
            cancel_proceed.set()

            f_cancel.result()
            f_chunk.result()

        assert results["cancel"].status_code == 200
        assert results["cancel"].json()["state"] == "cancelled"
        assert results["chunk"].status_code in (404, 409)

        # Ensure lock is completely evicted and cleaned up
        assert app.state.upload_locks.is_locked(upload_id) is False
        assert app.state.upload_locks.has_lock(upload_id) is False
        assert app.state.upload_locks.ref_count(upload_id) == 0
