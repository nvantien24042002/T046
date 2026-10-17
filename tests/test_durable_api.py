from dataclasses import replace
import hashlib
import secrets
import sqlite3
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from backend.durable_api import PREFIX, create_app
from storage.config import UploadConfig
from storage.uploads import UploadStore


@pytest.fixture
def api(tmp_path):
    config = UploadConfig(tmp_path / "private", secrets.token_urlsafe(32),
        max_bytes=1024, chunk_bytes=8, block_bytes=3, min_free_bytes=0)
    owners = {"alice": secrets.token_urlsafe(32), "bob": secrets.token_urlsafe(32)}
    app = create_app(config, owners)
    with TestClient(app, base_url="http://127.0.0.1:8011", client=("127.0.0.1", 12345)) as client:
        yield client, app, owners


def new(client, owners, size=16, request_key=None):
    return client.post(PREFIX, headers={"X-Owner-Key": owners["alice"],
                       "Idempotency-Key": request_key or str(uuid4())},
                       json={"filename": "fixture.zip", "size_bytes": size})


def auth(owners, session):
    return {"X-Owner-Key": owners["alice"], "Upload-Capability": session["upload_capability"]}


def send(client, owners, session, data=b"abcdefgh", index=0, offset=0):
    return client.put(PREFIX + f"/{session['upload_id']}/chunks/{index}", content=data,
        headers={**auth(owners, session), "Upload-Offset": str(offset), "X-Chunk-SHA256": hashlib.sha256(data).hexdigest()})


def test_api_receipt_retry_state_and_no_registration(api):
    client, app, owners = api
    session = new(client, owners, size=8).json()
    assert session["state"] == "created" and session["received_bytes"] == 0
    assert send(client, owners, session).status_code == 200
    retry = send(client, owners, session)
    assert retry.status_code == 200 and retry.json()["replayed"]
    url = PREFIX + "/" + session["upload_id"]
    assert client.get(url + "/receipts", headers=auth(owners, session)).json()["receipts"][0]["length_bytes"] == 8
    done = client.post(url + "/complete", headers=auth(owners, session))
    assert done.status_code == 200 and done.json()["state"] == "uploaded"
    assert done.json()["durable"] and not done.json()["ready"] and not done.json()["validated"]
    assert str(app.state.store.root) not in done.text
    assert client.get("/api/v1/datasets/anything/summary", headers=auth(owners, session)).status_code == 404


def test_idempotent_creation_and_cross_owner_capability(api):
    client, _, owners = api
    request_key = str(uuid4())
    first = new(client, owners, request_key=request_key).json()
    assert new(client, owners, request_key=request_key).json() == first
    url = PREFIX + "/" + first["upload_id"]
    for headers in [{}, {"X-Owner-Key": owners["alice"]},
                    {"X-Owner-Key": owners["bob"], "Upload-Capability": first["upload_capability"]}]:
        for verb, path in [("get", url), ("get", url + "/receipts"), ("delete", url), ("post", url + "/complete")]:
            assert getattr(client, verb)(path, headers=headers).status_code == 403
    assert client.get(url, headers={**auth(owners, first), "Origin": "https://untrusted.example"}).status_code == 403
    assert client.get("/health", headers={"Host": "untrusted.example"}).status_code == 403


def test_wrong_hash_offset_and_oversize_rollback(api):
    client, app, owners = api
    session = new(client, owners).json()
    url = PREFIX + "/" + session["upload_id"]
    headers = {**auth(owners, session), "Upload-Offset": "0", "X-Chunk-SHA256": "0"*64}
    assert client.put(url + "/chunks/0", content=b"abcdefgh", headers=headers).status_code == 422
    assert send(client, owners, session, offset=1).status_code == 409
    assert send(client, owners, session, data=b"a"*9).status_code == 413
    assert send(client, owners, session, index=10**40).status_code == 422
    assert client.get(url + "/receipts?start=" + "9"*40, headers=auth(owners, session)).status_code == 422
    assert client.get(url, headers=auth(owners, session)).json()["received_bytes"] == 0
    assert app.state.store._file(session["upload_id"], "payload.part").stat().st_size == 0
    assert send(client, owners, session).status_code == 200


def test_cancel_tombstone_and_explicit_delete_of_unvalidated_upload(api):
    client, app, owners = api
    session = new(client, owners, size=8).json()
    send(client, owners, session)
    url = PREFIX + "/" + session["upload_id"]
    client.post(url + "/complete", headers=auth(owners, session))
    result = client.delete(url, headers=auth(owners, session))
    assert result.json()["state"] == "cancelled" and not result.json()["cleanup_pending"]
    assert result.json()["durable"] is False
    assert client.delete(url, headers=auth(owners, session)).status_code == 200
    assert not app.state.store._folder(session["upload_id"]).exists()
    assert client.get(url, headers=auth(owners, session)).json()["state"] == "cancelled"


def test_sqlite_failure_is_not_acknowledged_and_recovery_reopens(tmp_path):
    config = UploadConfig(tmp_path / "private", secrets.token_urlsafe(32), chunk_bytes=8, min_free_bytes=0)
    owners = {"alice": secrets.token_urlsafe(32)}
    def fail_commit(point):
        if point == "before_receipt_commit":
            raise sqlite3.OperationalError("synthetic commit failure")
    app = create_app(config, owners, store_factory=lambda cfg: UploadStore(cfg, fault=fail_commit))
    with TestClient(app, base_url="http://127.0.0.1:8011", client=("127.0.0.1", 1)) as client:
        session = new(client, owners).json()
        assert send(client, owners, session).status_code == 503
        assert client.get("/health").status_code == 503
    app = create_app(config, owners)
    with TestClient(app, base_url="http://127.0.0.1:8011", client=("127.0.0.1", 1)) as client:
        url = PREFIX + "/" + session["upload_id"]
        assert client.get(url, headers=auth(owners, session)).json()["received_bytes"] == 0
        assert send(client, owners, session).json()["received_bytes"] == 8


def test_metadata_body_and_unknown_paths_rejected(api):
    client, app, owners = api
    assert new(client, owners, size=1025).status_code == 413
    assert client.post(PREFIX, content=b"x"*4097,
                       headers={"X-Owner-Key": owners["alice"]}).status_code == 413
    response = client.post(PREFIX, json={"filename": "fixture.zip", "size_bytes": 8, "path": "outside"},
        headers={"X-Owner-Key": owners["alice"], "Idempotency-Key": str(uuid4())})
    assert response.status_code == 422
    assert app.state.store.db.execute("SELECT COUNT(*) FROM uploads").fetchone()[0] == 0
