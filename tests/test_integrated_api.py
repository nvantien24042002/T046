import hashlib
import json
from pathlib import Path
import secrets
import uuid
import zipfile

from fastapi.testclient import TestClient
import pytest

from backend.integrated_api import PREFIX, DATASET_PREFIX, create_integrated_app
from datasets.limits import Limits
from storage.config import UploadConfig
from tests.fixtures import make_zip, XML


@pytest.fixture
def integrated_api(tmp_path):
    root = tmp_path / "private_integrated"
    config = UploadConfig(
        root,
        secrets.token_urlsafe(32),
        max_bytes=500000,
        chunk_bytes=16384,
        block_bytes=4096,
        min_free_bytes=0,
        quota_bytes=10**9,
        idle_seconds=10,
        lifetime_seconds=60,
    )
    owners = {"alice": secrets.token_urlsafe(32), "bob": secrets.token_urlsafe(32)}
    app = create_integrated_app(config, owners, limits=Limits())
    with TestClient(app, base_url="http://127.0.0.1:8011", client=("127.0.0.1", 12345)) as client:
        yield client, app, owners


def auth(owners, session):
    return {"X-Owner-Key": owners["alice"], "Upload-Capability": session["upload_capability"]}


def upload_fixture_via_api(client, owners, data, filename="fixture.zip"):
    # 1. Create upload
    res = client.post(
        PREFIX,
        headers={"X-Owner-Key": owners["alice"], "Idempotency-Key": str(uuid.uuid4())},
        json={"filename": filename, "size_bytes": len(data)},
    )
    assert res.status_code == 201, res.text
    session = res.json()
    upload_id = session["upload_id"]

    # 2. Upload chunk
    chunk_res = client.put(
        f"{PREFIX}/{upload_id}/chunks/0",
        content=data,
        headers={
            **auth(owners, session),
            "Upload-Offset": "0",
            "X-Chunk-SHA256": hashlib.sha256(data).hexdigest(),
        },
    )
    assert chunk_res.status_code == 200, chunk_res.text

    # 3. Complete
    complete_res = client.post(f"{PREFIX}/{upload_id}/complete", headers=auth(owners, session))
    assert complete_res.status_code == 200
    assert complete_res.json()["state"] == "uploaded"
    assert complete_res.json()["ready"] is False
    return session


def test_full_upload_validation_analysis_lifecycle(integrated_api, tmp_path):
    client, app, owners = integrated_api
    archive_path = tmp_path / "fixture.zip"
    make_zip(archive_path)
    data = archive_path.read_bytes()

    # 1. Upload
    session = upload_fixture_via_api(client, owners, data)
    upload_id = session["upload_id"]

    # Check status before validation: uploaded but dataset not requested
    ds_status = client.get(f"{PREFIX}/{upload_id}/dataset", headers=auth(owners, session)).json()
    assert ds_status["state"] == "not_requested"
    assert ds_status["ready"] is False

    # 2. Validate
    val_res = client.post(f"{PREFIX}/{upload_id}/validate", headers=auth(owners, session))
    assert val_res.status_code == 200, val_res.text
    val_data = val_res.json()
    assert val_data["state"] == "ready"
    assert val_data["ready"] is True
    assert val_data["error_code"] is None
    dataset_id = val_data["dataset_id"]

    # 3. Query Summary
    sum_res = client.get(f"{DATASET_PREFIX}/{dataset_id}/summary", headers=auth(owners, session))
    assert sum_res.status_code == 200, sum_res.text
    summary = sum_res.json()
    assert summary["dataset_id"] == dataset_id
    assert summary["state"] == "ready"
    assert summary["ready"] is True
    assert summary["entries"] == 3
    assert summary["images"] == 2
    assert summary["annotations"] is True
    assert summary["capabilities"]["image_quality"] is True
    assert summary["capabilities"]["label_audit"] is True

    # 4. Results before analysis -> 409 ANALYSIS_NOT_RUN (never implicit full processing on GET)
    res_before = client.get(f"{DATASET_PREFIX}/{dataset_id}/results", headers=auth(owners, session))
    assert res_before.status_code == 409
    assert res_before.json()["error"]["code"] == "ANALYSIS_NOT_RUN"

    # 5. Explicit Analysis POST
    analyze_res = client.post(f"{DATASET_PREFIX}/{dataset_id}/analyze", headers=auth(owners, session), json={})
    assert analyze_res.status_code == 200, analyze_res.text
    analysis = analyze_res.json()
    assert analysis["dataset_id"] == dataset_id
    assert len(analysis["quality_rows"]) == 2
    assert len(analysis["audit_rows"]) > 0
    assert len(analysis["classes"]) > 0

    # 6. Query cached results after analysis
    res_after = client.get(f"{DATASET_PREFIX}/{dataset_id}/results", headers=auth(owners, session))
    assert res_after.status_code == 200
    assert res_after.json() == analysis

    # 7. Previews
    preview_res = client.get(
        f"{DATASET_PREFIX}/{dataset_id}/preview",
        params={"image_path": "images/flat.png"},
        headers=auth(owners, session),
    )
    assert preview_res.status_code == 200
    assert preview_res.headers["content-type"] == "image/png"
    assert len(preview_res.content) > 0

    # Annotated preview
    annotated_res = client.get(
        f"{DATASET_PREFIX}/{dataset_id}/preview",
        params={"image_path": "images/flat.png", "annotated": "true"},
        headers=auth(owners, session),
    )
    assert annotated_res.status_code == 200
    assert len(annotated_res.content) > 0


def test_unvalidated_upload_cannot_be_queried_as_ready(integrated_api, tmp_path):
    client, app, owners = integrated_api
    archive_path = tmp_path / "fixture.zip"
    make_zip(archive_path)
    data = archive_path.read_bytes()

    # Upload only, do not validate
    session = upload_fixture_via_api(client, owners, data)
    upload_id = session["upload_id"]

    # Status shows uploaded, not ready
    st = client.get(f"{PREFIX}/{upload_id}", headers=auth(owners, session)).json()
    assert st["state"] == "uploaded"
    assert st["ready"] is False

    # Attempting to analyze or query summary of nonexistent/unready dataset returns 404 or 409
    fake_id = str(uuid.uuid4())
    res = client.get(f"{DATASET_PREFIX}/{fake_id}/summary", headers=auth(owners, session))
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "DATASET_NOT_FOUND"


def test_invalid_zip_never_reaches_ready(integrated_api):
    client, app, owners = integrated_api
    corrupt_data = b"NOT_A_VALID_ZIP_ARCHIVE_DATA" * 5

    session = upload_fixture_via_api(client, owners, corrupt_data)
    upload_id = session["upload_id"]

    # Validate fails
    val_res = client.post(f"{PREFIX}/{upload_id}/validate", headers=auth(owners, session))
    assert val_res.status_code == 200
    val_data = val_res.json()
    assert val_data["state"] == "failed"
    assert val_data["ready"] is False
    assert val_data["error_code"] is not None

    dataset_id = val_data["dataset_id"]
    # Query summary for failed dataset reflects failed state
    sum_res = client.get(f"{DATASET_PREFIX}/{dataset_id}/summary", headers=auth(owners, session)).json()
    assert sum_res["state"] == "failed"
    assert sum_res["ready"] is False

    # Analysis on failed dataset returns 409
    an_res = client.post(f"{DATASET_PREFIX}/{dataset_id}/analyze", headers=auth(owners, session))
    assert an_res.status_code == 409
    assert an_res.json()["error"]["code"] == "DATASET_NOT_READY"


def test_xml_attack_never_reaches_ready(integrated_api, tmp_path):
    from tests.test_archive_validation import make_minimal_image_png
    client, app, owners = integrated_api
    bad_zip = tmp_path / "attack.zip"
    dtd_xml = b'<!DOCTYPE foo [<!ENTITY test "hacked">]><annotations><image id="1" name="a.png" width="10" height="10"/></annotations>'
    with zipfile.ZipFile(bad_zip, "w") as z:
        z.writestr("images/a.png", make_minimal_image_png())
        z.writestr("annotations.xml", dtd_xml)

    session = upload_fixture_via_api(client, owners, bad_zip.read_bytes())
    upload_id = session["upload_id"]

    val_res = client.post(f"{PREFIX}/{upload_id}/validate", headers=auth(owners, session)).json()
    assert val_res["state"] == "failed"
    assert val_res["error_code"] == "XML_DTD_OR_ENTITY_FORBIDDEN"


def test_api_responses_never_leak_paths_or_secrets(integrated_api):
    client, app, owners = integrated_api

    # Bad auth
    unauth = client.get(f"{PREFIX}/unknown", headers={"X-Owner-Key": "wrong_key"})
    assert unauth.status_code == 403
    assert unauth.json() == {"error": {"code": "ACCESS_DENIED"}}

    # Bad metadata (invalid filename with path traversal)
    bad_meta = client.post(
        PREFIX,
        headers={"X-Owner-Key": owners["alice"], "Idempotency-Key": str(uuid.uuid4())},
        json={"filename": "../../etc/passwd.zip", "size_bytes": 100},
    )
    assert bad_meta.status_code == 422
    assert "error" in bad_meta.json()
    # Ensure no local drive paths in response
    assert "D:\\" not in bad_meta.text and "C:\\" not in bad_meta.text


def test_duplicate_validation_requests(integrated_api, tmp_path):
    client, app, owners = integrated_api
    archive_path = tmp_path / "fixture.zip"
    make_zip(archive_path)
    session = upload_fixture_via_api(client, owners, archive_path.read_bytes())
    upload_id = session["upload_id"]

    # First validation succeeds
    res1 = client.post(f"{PREFIX}/{upload_id}/validate", headers=auth(owners, session))
    assert res1.status_code == 200
    assert res1.json()["state"] == "ready"

    # Repeated validation on ready dataset is idempotent
    res2 = client.post(f"{PREFIX}/{upload_id}/validate", headers=auth(owners, session))
    assert res2.status_code == 200
    assert res2.json()["state"] == "ready"
    assert res2.json()["dataset_id"] == res1.json()["dataset_id"]


def test_interrupted_worker_recovery(integrated_api, tmp_path):
    client, app, owners = integrated_api
    archive_path = tmp_path / "fixture.zip"
    make_zip(archive_path)
    session = upload_fixture_via_api(client, owners, archive_path.read_bytes())
    upload_id = session["upload_id"]

    # Simulate process crash leaving validation_datasets in 'validating'
    attempt_id = str(uuid.uuid4())
    dataset_id = str(uuid.uuid4())
    with app.state.store.transaction():
        app.state.store.db.execute(
            "INSERT INTO validation_datasets (upload_id,dataset_id,attempt_id,state) VALUES (?,?,?,'validating')",
            (upload_id, dataset_id, attempt_id),
        )
        app.state.store.db.execute(
            "INSERT INTO validation_attempts VALUES (?,?,'active',?)",
            (attempt_id, upload_id, 1024),
        )

    # Re-run recovery via manager
    app.state.manager._recover()

    # Querying lifecycle status reflects truthful recovery to failed
    st = client.get(f"{PREFIX}/{upload_id}/dataset", headers=auth(owners, session)).json()
    assert st["state"] == "failed"
    assert st["error_code"] == "WORKER_INTERRUPTED"


def test_cross_owner_isolation(integrated_api, tmp_path):
    client, app, owners = integrated_api
    archive_path = tmp_path / "fixture.zip"
    make_zip(archive_path)
    session = upload_fixture_via_api(client, owners, archive_path.read_bytes())
    upload_id = session["upload_id"]
    val = client.post(f"{PREFIX}/{upload_id}/validate", headers=auth(owners, session)).json()
    dataset_id = val["dataset_id"]

    # Bob attempts to access Alice's dataset summary
    bob_headers = {"X-Owner-Key": owners["bob"], "Upload-Capability": session["upload_capability"]}
    bob_res = client.get(f"{DATASET_PREFIX}/{dataset_id}/summary", headers=bob_headers)
    assert bob_res.status_code == 403
    assert bob_res.json()["error"]["code"] == "ACCESS_DENIED"


def test_pipeline_failure_does_not_publish_partial_results(integrated_api, tmp_path, monkeypatch):
    client, app, owners = integrated_api
    archive_path = tmp_path / "fixture.zip"
    make_zip(archive_path)
    session = upload_fixture_via_api(client, owners, archive_path.read_bytes())
    upload_id = session["upload_id"]
    val = client.post(f"{PREFIX}/{upload_id}/validate", headers=auth(owners, session)).json()
    dataset_id = val["dataset_id"]

    # Monkeypatch execute_pipeline to raise an error during computation
    def failing_pipeline(*args, **kwargs):
        raise RuntimeError("SIMULATED_CORE_PIPELINE_ERROR")

    monkeypatch.setattr("backend.integrated_api.execute_pipeline", failing_pipeline)

    # Trigger analysis -> should fail with 500 without leaking stack trace or paths
    an_res = client.post(f"{DATASET_PREFIX}/{dataset_id}/analyze", headers=auth(owners, session), json={})
    assert an_res.status_code == 500
    assert an_res.json() == {"error": {"code": "PIPELINE_EXECUTION_FAILED"}}
    assert "SIMULATED_CORE_PIPELINE_ERROR" not in an_res.text
    assert "Traceback" not in an_res.text

    # Results endpoint must still return 409 ANALYSIS_NOT_RUN (no partial or corrupt cache published)
    res_check = client.get(f"{DATASET_PREFIX}/{dataset_id}/results", headers=auth(owners, session))
    assert res_check.status_code == 409
    assert res_check.json()["error"]["code"] == "ANALYSIS_NOT_RUN"


def test_concurrent_validation_requests(integrated_api, tmp_path):
    import concurrent.futures
    client, app, owners = integrated_api
    archive_path = tmp_path / "fixture.zip"
    make_zip(archive_path)
    session = upload_fixture_via_api(client, owners, archive_path.read_bytes())
    upload_id = session["upload_id"]

    def trigger_validation():
        return client.post(f"{PREFIX}/{upload_id}/validate", headers=auth(owners, session))

    # Send concurrent requests
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
        futs = [executor.submit(trigger_validation) for _ in range(3)]
        responses = [f.result() for f in futs]

    # All should succeed (first validates, subsequent return existing ready or serialized status)
    for res in responses:
        assert res.status_code == 200
        assert res.json()["state"] == "ready"
        assert res.json()["ready"] is True

    # Single canonical dataset_id
    dataset_ids = {r.json()["dataset_id"] for r in responses}
    assert len(dataset_ids) == 1


def test_dataset_cancellation(integrated_api, tmp_path):
    client, app, owners = integrated_api
    archive_path = tmp_path / "fixture.zip"
    make_zip(archive_path)
    session = upload_fixture_via_api(client, owners, archive_path.read_bytes())
    upload_id = session["upload_id"]

    # Calling cancel before validation requested returns 409 NO_CANCELLABLE_VALIDATION
    cancel_res = client.post(f"{PREFIX}/{upload_id}/dataset/cancel", headers=auth(owners, session))
    assert cancel_res.status_code == 409
    assert cancel_res.json()["error"]["code"] == "NO_CANCELLABLE_VALIDATION"

    # Simulate an active validation attempt
    attempt_id = str(uuid.uuid4())
    dataset_id = str(uuid.uuid4())
    with app.state.store.transaction():
        app.state.store.db.execute(
            "INSERT INTO validation_datasets (upload_id,dataset_id,attempt_id,state,cancel_requested) VALUES (?,?,?,'validating',0)",
            (upload_id, dataset_id, attempt_id),
        )
        app.state.store.db.execute(
            "INSERT INTO validation_attempts VALUES (?,?,'active',?)",
            (attempt_id, upload_id, 1024),
        )

    # Now cancel succeeds and sets cancel_requested
    cancel_res2 = client.post(f"{PREFIX}/{upload_id}/dataset/cancel", headers=auth(owners, session))
    assert cancel_res2.status_code == 200
    assert cancel_res2.json()["state"] == "validating"

    # Status reflects cancel requested
    row = app.state.store.db.execute("SELECT cancel_requested FROM validation_datasets WHERE upload_id=?", (upload_id,)).fetchone()
    assert row["cancel_requested"] == 1


