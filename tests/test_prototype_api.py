import hashlib
from pathlib import Path
import secrets

import pytest
from fastapi.testclient import TestClient

from prototype.api import CHUNK_BYTES, MAX_FILE_BYTES, PREFIX, Settings, create_app
from tests.fixtures import make_zip


@pytest.fixture
def setup_client(tmp_path):
    key = secrets.token_urlsafe(32)
    app = create_app(Settings(tmp_path / "private", key))
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as client:
        yield client, app, key


def init(client, key, size):
    return client.post(PREFIX, json={"filename": "fixture.zip", "size_bytes": size},
                       headers={"X-Prototype-Key": key})


def test_small_zip_chunk_bytes_and_hash(setup_client, tmp_path):
    client, app, key = setup_client
    path = make_zip(tmp_path / "fixture.zip", filler=3 * CHUNK_BYTES)
    session = init(client, key, path.stat().st_size).json()
    auth = {"Authorization": "Bearer " + session["upload_token"]}
    url = PREFIX + "/" + session["upload_id"]
    expected_hash = hashlib.sha256()
    offset = 0
    with path.open("rb") as source:
        for index, chunk in enumerate(iter(lambda: source.read(CHUNK_BYTES), b"")):
            expected_hash.update(chunk)
            response = client.put(url + f"/chunks/{index}", content=chunk,
                                  headers={**auth, "Upload-Offset": str(offset)})
            assert response.status_code == 200, response.text
            offset += len(chunk)
            assert response.json()["received_bytes"] == offset
    done = client.post(url + "/complete", headers=auth)
    assert done.status_code == 200
    assert done.json()["sha256"] == expected_hash.hexdigest()
    assert done.json()["state"] == "received_unvalidated"
    assert done.json()["ready"] is False and done.json()["durable"] is False
    assert "path" not in done.text
    stored = app.state.sessions[session["upload_id"]].path
    with stored.open("rb") as file:
        assert hashlib.file_digest(file, "sha256").hexdigest() == expected_hash.hexdigest()


@pytest.mark.parametrize("size", [0, -1, MAX_FILE_BYTES + 1, True])
def test_reject_size(setup_client, size):
    client, _, key = setup_client
    assert init(client, key, size).status_code == 422


def test_access_origin_and_host(setup_client):
    client, _, key = setup_client
    assert init(client, "wrong", 10).status_code == 403
    assert client.post(PREFIX, headers={"Origin": "https://untrusted.example", "X-Prototype-Key": key}).status_code == 403
    assert client.get("/health", headers={"Host": "untrusted.example"}).status_code == 403
    session = init(client, key, 10).json()
    url = PREFIX + "/" + session["upload_id"]
    assert client.get(url).status_code == 403
    assert client.get(PREFIX + "/unknown").status_code == 404
    preflight = client.options(PREFIX, headers={"Origin": "http://127.0.0.1:8502",
                 "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-prototype-key"})
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://127.0.0.1:8502"


def test_incomplete_bad_offset_and_size_rollback(setup_client):
    client, app, key = setup_client
    session = init(client, key, 10).json()
    auth = {"Authorization": "Bearer " + session["upload_token"]}
    url = PREFIX + "/" + session["upload_id"]
    assert client.post(url + "/complete", headers=auth).status_code == 409
    assert client.put(url + "/chunks/1", content=b"1234567890", headers={**auth, "Upload-Offset": "0"}).status_code == 409
    for body, code in [(b"short", 422), (b"too many bytes", 413)]:
        assert client.put(url + "/chunks/0", content=body, headers={**auth, "Upload-Offset": "0"}).status_code == code
        assert client.get(url, headers=auth).json()["received_bytes"] == 0
        assert app.state.sessions[session["upload_id"]].path.stat().st_size == 0
    assert init(client, key, 10).status_code == 409


def test_no_dataset_ready_or_durability_route(setup_client):
    client, _, _ = setup_client
    assert client.get("/api/v1/datasets/anything/summary").status_code == 404
    assert client.get("/health").json()["durable"] is False


def test_missing_or_short_secret_rejected(tmp_path):
    with pytest.raises(ValueError, match="32 random"):
        Settings(tmp_path, "")
