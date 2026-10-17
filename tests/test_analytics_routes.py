"""Tests for Track B verified result routes."""
from datetime import datetime, timezone
from pathlib import Path
import threading
import sqlite3

from fastapi.testclient import TestClient
import pytest

from backend.analytics_configuration import (
    CANONICALIZATION_V1,
    PRODUCTION_REGISTRY,
    config_id_v1,
    get_default_effective_config,
    payload_sha256_v1,
)
from backend.analytics_contracts import AUDIENCE, ORIGIN, VERSION
from backend.analytics_grants import GrantStore, IssuerRecord
from backend.analytics_intermediary import create_analytics_app
from backend.analytics_storage import ProvenanceRecord, ResultStore
from tests.analytics_support import Clock, FakeIssuer, FakeUpstream


@pytest.fixture
def store(tmp_path):
    """Create a temporary result store in isolated test path."""
    return ResultStore(tmp_path / "results.db")


@pytest.fixture
def sample_payload():
    return {
        "dataset_id": "test_ds_001",
        "quality_rows": [{"filename": "a.jpg", "score": 85}],
        "audit_rows": [{"box_id": "box1", "status": "ok"}],
    }


def make_provenance(result_id, dataset_id, payload, effective_config=None, archive_sha256=None):
    if effective_config is None:
        effective_config = get_default_effective_config()
    if archive_sha256 is None:
        archive_sha256 = "a" * 64

    return ProvenanceRecord(
        result_id=result_id,
        dataset_id=dataset_id,
        archive_sha256=archive_sha256,
        config_id=config_id_v1(
            "config-2026-10-17-01",
            "defaults-2026-10-17-01",
            CANONICALIZATION_V1,
            effective_config,
        ),
        configuration_schema_revision="config-2026-10-17-01",
        defaults_version="defaults-2026-10-17-01",
        canonicalization_version=CANONICALIZATION_V1,
        algorithm_version="algo-core-v1",
        producer_build_id="build_123",
        producer_commit="commit_abc",
        producer_timestamp=datetime.now(timezone.utc).isoformat(),
        request_fingerprint="f" * 64,
        payload_sha256=payload_sha256_v1(payload),
        published_at=datetime.now(timezone.utc).isoformat(),
    )


@pytest.fixture
def app_with_store(store):
    """Create app with Track B verified routes and test dependencies."""
    clock = Clock()
    issuer = FakeIssuer({})
    grants = GrantStore(clock=clock)

    summary_data = {
        "dataset_id": "test_ds_001",
        "upload_id": "upload_001",
        "state": "ready",
        "archive_sha256": "a" * 64,
        "ready": True,
        "archive_bytes": 1000,
        "entries": 10,
        "images": 10,
        "annotations": True,
        "capabilities": {
            "image_quality": True,
            "label_audit": True,
            "distribution": False,
            "coverage": False,
            "scene_finder": False,
        },
    }
    upstream = FakeUpstream(value=summary_data)

    app = create_analytics_app(
        issuer=issuer,
        grants=grants,
        upstream=upstream,
        store=store,
        revisions=PRODUCTION_REGISTRY,
    )
    app.state.clock = clock
    return app


@pytest.fixture
def client_with_grant(app_with_store):
    """Create test client with valid grant."""
    now = int(app_with_store.state.clock())
    record = IssuerRecord(
        issued_at=now,
        audience=AUDIENCE,
        origin=ORIGIN,
        dataset_id="test_ds_001",
        scopes=frozenset({"summary:read", "results:read"}),
        result_ids=frozenset({"result_20261017_001"}),
        upstream_capability="test-capability",
    )

    app_with_store.state.issuer.records["test-code"] = record

    client = TestClient(app_with_store, base_url=AUDIENCE, client=("127.0.0.1", 1234), headers={"Origin": ORIGIN})
    response = client.post("/analytics/v1/grants/exchange", json={"exchange_code": "test-code"})
    assert response.status_code == 200

    token = response.json()["access_token"]
    return client, {"Authorization": f"Bearer {token}"}


def test_result_list_empty(client_with_grant):
    """Empty result list when no results published."""
    client, headers = client_with_grant
    response = client.get("/analytics/v1/datasets/test_ds_001/results", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == VERSION
    assert data["dataset_id"] == "test_ds_001"
    assert data["results"] == []
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Access-Control-Allow-Origin"] == ORIGIN


def test_result_list_returns_authorized_results(client_with_grant, store, sample_payload):
    """List returns only authorized results for the grant."""
    client, headers = client_with_grant

    effective_config = get_default_effective_config()
    payload1 = sample_payload
    payload2 = {
        "dataset_id": "test_ds_001",
        "quality_rows": [{"filename": "b.jpg", "score": 90}],
        "audit_rows": [],
    }

    prov1 = make_provenance("result_20261017_001", "test_ds_001", payload1, effective_config)
    prov2 = make_provenance("result_20261017_002", "test_ds_001", payload2, effective_config)

    store.publish(prov1, payload1, effective_config.model_dump())
    store.publish(prov2, payload2, effective_config.model_dump())

    response = client.get("/analytics/v1/datasets/test_ds_001/results", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 1
    assert data["results"][0]["result_id"] == "result_20261017_001"
    assert data["results"][0]["verification"] == "verified"


def test_result_list_filters_by_authorization(client_with_grant, store):
    """Authorization filter rejects results not in grant result_ids."""
    client, headers = client_with_grant

    effective_config = get_default_effective_config()
    payload = {"dataset_id": "test_ds_001", "quality_rows": [], "audit_rows": []}
    prov = make_provenance("result_unauthorized", "test_ds_001", payload, effective_config)

    store.publish(prov, payload, effective_config.model_dump())

    response = client.get("/analytics/v1/datasets/test_ds_001/results", headers=headers)
    assert response.status_code == 200
    assert response.json()["results"] == []


def test_result_detail_succeeds(client_with_grant, store, sample_payload):
    """Retrieve authorized result detail with full verified envelope."""
    client, headers = client_with_grant

    effective_config = get_default_effective_config()
    prov = make_provenance("result_20261017_001", "test_ds_001", sample_payload, effective_config)
    store.publish(prov, sample_payload, effective_config.model_dump())

    response = client.get(
        "/analytics/v1/datasets/test_ds_001/results/result_20261017_001",
        headers=headers,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["schema_version"] == VERSION
    assert data["descriptor"]["result_id"] == "result_20261017_001"
    assert data["descriptor"]["dataset_id"] == "test_ds_001"
    assert data["provenance"]["verification"] == "verified"
    assert data["provenance"]["config_id"] == prov.config_id
    assert data["provenance"]["payload_sha256"] == prov.payload_sha256
    assert data["payload"]["quality_rows"] == sample_payload["quality_rows"]
    assert data["payload"]["audit_rows"] == sample_payload["audit_rows"]


def test_result_detail_not_found(client_with_grant, app_with_store):
    """404 when authorized result does not exist in store."""
    client, _ = client_with_grant

    now = int(app_with_store.state.clock())
    record = IssuerRecord(
        issued_at=now,
        audience=AUDIENCE,
        origin=ORIGIN,
        dataset_id="test_ds_001",
        scopes=frozenset({"summary:read", "results:read"}),
        result_ids=frozenset({"nonexistent"}),
        upstream_capability="test-capability",
    )
    app_with_store.state.issuer.records["code-nonexistent"] = record

    res = client.post("/analytics/v1/grants/exchange", json={"exchange_code": "code-nonexistent"})
    assert res.status_code == 200
    headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

    response = client.get("/analytics/v1/datasets/test_ds_001/results/nonexistent", headers=headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESULT_NOT_FOUND"


def test_result_detail_unauthorized(client_with_grant, store, sample_payload):
    """403 ACCESS_SCOPE_DENIED when result exists but is not in grant's result_ids."""
    client, headers = client_with_grant

    effective_config = get_default_effective_config()
    prov = make_provenance("result_unauthorized", "test_ds_001", sample_payload, effective_config)
    store.publish(prov, sample_payload, effective_config.model_dump())

    response = client.get(
        "/analytics/v1/datasets/test_ds_001/results/result_unauthorized",
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCESS_SCOPE_DENIED"


def test_result_list_dataset_not_ready(app_with_store):
    """409 DATASET_NOT_READY when dataset is not ready."""
    not_ready_data = {
        "dataset_id": "test_ds_002",
        "upload_id": "upload_002",
        "state": "indexing",
        "ready": False,
    }
    app_with_store.state.upstream.value = not_ready_data

    now = int(app_with_store.state.clock())
    record = IssuerRecord(
        issued_at=now,
        audience=AUDIENCE,
        origin=ORIGIN,
        dataset_id="test_ds_002",
        scopes=frozenset({"summary:read", "results:read"}),
        result_ids=frozenset(),
        upstream_capability="test-capability",
    )
    app_with_store.state.issuer.records["test-code-2"] = record

    client = TestClient(app_with_store, base_url=AUDIENCE, client=("127.0.0.1", 1234), headers={"Origin": ORIGIN})
    response = client.post("/analytics/v1/grants/exchange", json={"exchange_code": "test-code-2"})
    assert response.status_code == 200
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/analytics/v1/datasets/test_ds_002/results", headers=headers)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DATASET_NOT_READY"


def test_result_list_requires_results_scope(app_with_store):
    """403 ACCESS_SCOPE_DENIED when grant lacks results:read scope."""
    now = int(app_with_store.state.clock())
    record = IssuerRecord(
        issued_at=now,
        audience=AUDIENCE,
        origin=ORIGIN,
        dataset_id="test_ds_001",
        scopes=frozenset({"summary:read"}),
        result_ids=frozenset(),
        upstream_capability="test-capability",
    )
    app_with_store.state.issuer.records["test-code-no-results"] = record

    client = TestClient(app_with_store, base_url=AUDIENCE, client=("127.0.0.1", 1234), headers={"Origin": ORIGIN})
    response = client.post("/analytics/v1/grants/exchange", json={"exchange_code": "test-code-no-results"})
    assert response.status_code == 200
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/analytics/v1/datasets/test_ds_001/results", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCESS_SCOPE_DENIED"


def test_missing_authorization_header(app_with_store):
    """401 ACCESS_GRANT_INVALID when authorization header is absent."""
    client = TestClient(app_with_store, base_url=AUDIENCE, client=("127.0.0.1", 1234), headers={"Origin": ORIGIN})
    response = client.get("/analytics/v1/datasets/test_ds_001/results")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "ACCESS_GRANT_INVALID"


def test_expired_grant(client_with_grant, app_with_store):
    """401 ACCESS_GRANT_INVALID when grant has expired."""
    client, headers = client_with_grant
    # Advance clock past grant lifetime (600 seconds)
    app_with_store.state.clock.value += 601

    response = client.get("/analytics/v1/datasets/test_ds_001/results", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "ACCESS_GRANT_INVALID"


def test_revoked_grant(client_with_grant, app_with_store):
    """401 ACCESS_GRANT_INVALID when grant token was revoked."""
    client, headers = client_with_grant
    token = headers["Authorization"].removeprefix("Bearer ")
    assert app_with_store.state.grants.revoke(token) is True

    response = client.get("/analytics/v1/datasets/test_ds_001/results", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "ACCESS_GRANT_INVALID"


def test_dataset_id_isolation(client_with_grant):
    """403 ACCESS_SCOPE_DENIED when attempting cross-dataset access."""
    client, headers = client_with_grant
    response = client.get("/analytics/v1/datasets/other_ds/results", headers=headers)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCESS_SCOPE_DENIED"


@pytest.mark.parametrize("origin", [None, "null", "http://evil.invalid", "http://127.0.0.1:5174"])
def test_origin_validation(client_with_grant, origin):
    """403 LOCAL_ONLY for disallowed origins."""
    client, headers = client_with_grant
    custom_headers = dict(headers)
    if origin is None:
        custom_headers.pop("Origin", None)
    else:
        custom_headers["Origin"] = origin

    req = client.build_request("GET", "/analytics/v1/datasets/test_ds_001/results")
    req.headers.pop("origin", None)
    if origin is not None:
        req.headers["origin"] = origin
    req.headers["authorization"] = custom_headers["Authorization"]

    response = client.send(req)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "LOCAL_ONLY"


def test_tampered_persisted_payload_detected(client_with_grant, store, sample_payload):
    """502 UPSTREAM_RESULT_INVALID when persisted payload was silently mutated in db."""
    client, headers = client_with_grant

    effective_config = get_default_effective_config()
    prov = make_provenance("result_20261017_001", "test_ds_001", sample_payload, effective_config)
    store.publish(prov, sample_payload, effective_config.model_dump())

    # Direct database tamper simulation
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        "UPDATE payloads SET payload_json = ? WHERE result_id = ?",
        ('{"dataset_id": "test_ds_001", "tampered": true}', "result_20261017_001")
    )
    conn.commit()
    conn.close()

    response = client.get(
        "/analytics/v1/datasets/test_ds_001/results/result_20261017_001",
        headers=headers,
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_RESULT_INVALID"


def test_tampered_persisted_config_detected(client_with_grant, store, sample_payload):
    """502 UPSTREAM_RESULT_INVALID when persisted effective config was tampered in db."""
    client, headers = client_with_grant

    effective_config = get_default_effective_config()
    prov = make_provenance("result_20261017_001", "test_ds_001", sample_payload, effective_config)
    store.publish(prov, sample_payload, effective_config.model_dump())

    # Direct database tamper of effective_config_json
    tampered_cfg = effective_config.model_dump()
    tampered_cfg["image_cfg"]["dark_threshold"] = {"kind": "integer", "decimal": "99"}
    import json
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        "UPDATE provenance SET effective_config_json = ? WHERE result_id = ?",
        (json.dumps(tampered_cfg), "result_20261017_001")
    )
    conn.commit()
    conn.close()

    response = client.get(
        "/analytics/v1/datasets/test_ds_001/results/result_20261017_001",
        headers=headers,
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_RESULT_INVALID"


def test_archive_hash_mismatch_with_summary(client_with_grant, store, sample_payload):
    """502 UPSTREAM_RESULT_INVALID when descriptor archive_sha256 does not match summary."""
    client, headers = client_with_grant

    effective_config = get_default_effective_config()
    # Different archive sha
    prov = make_provenance("result_20261017_001", "test_ds_001", sample_payload, effective_config, archive_sha256="b" * 64)
    store.publish(prov, sample_payload, effective_config.model_dump())

    response = client.get(
        "/analytics/v1/datasets/test_ds_001/results/result_20261017_001",
        headers=headers,
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_RESULT_INVALID"


def test_duplicate_publication_rejected(store, sample_payload):
    """Duplicate result_id publication is rejected atomically."""
    effective_config = get_default_effective_config()
    prov = make_provenance("result_duplicate", "test_ds_001", sample_payload, effective_config)

    store.publish(prov, sample_payload, effective_config.model_dump())
    with pytest.raises(ValueError, match="already published"):
        store.publish(prov, sample_payload, effective_config.model_dump())


def test_concurrent_publication_serialized(store, sample_payload):
    """Concurrent publications succeed without corruption."""
    effective_config = get_default_effective_config()
    errors = []

    def publish_worker(idx):
        try:
            pid = f"result_concurrent_{idx}"
            payload = {**sample_payload, "result_idx": idx}
            prov = make_provenance(pid, "test_ds_001", payload, effective_config)
            store.publish(prov, payload, effective_config.model_dump())
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=publish_worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    results = store.list_results("test_ds_001")
    assert len(results) == 5


def test_preview_authorized_success(client_with_grant):
    client, headers = client_with_grant
    resp = client.get(
        "/analytics/v1/datasets/test_ds_001/preview?image_path=flat.png",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content.startswith(b"\x89PNG")


def test_preview_annotated_success(client_with_grant):
    client, headers = client_with_grant
    resp = client.get(
        "/analytics/v1/datasets/test_ds_001/preview?image_path=flat.png&annotated=true",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_preview_missing_grant(client_with_grant):
    client, _ = client_with_grant
    resp = client.get("/analytics/v1/datasets/test_ds_001/preview?image_path=flat.png")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "ACCESS_GRANT_INVALID"


def test_preview_invalid_path(client_with_grant):
    client, headers = client_with_grant
    resp = client.get(
        "/analytics/v1/datasets/test_ds_001/preview?image_path=../secret.png",
        headers=headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "INVALID_PATH"


def test_preview_image_not_found(client_with_grant):
    client, headers = client_with_grant
    resp = client.get(
        "/analytics/v1/datasets/test_ds_001/preview?image_path=missing.png",
        headers=headers,
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "IMAGE_NOT_FOUND"


def test_preview_dataset_not_ready(app_with_store):
    not_ready_data = {
        "dataset_id": "test_ds_001",
        "upload_id": "upload_001",
        "state": "validating",
        "ready": False,
    }
    app_with_store.state.upstream.value = not_ready_data

    now = int(app_with_store.state.clock())
    record = IssuerRecord(
        issued_at=now,
        audience=AUDIENCE,
        origin=ORIGIN,
        dataset_id="test_ds_001",
        scopes=frozenset({"summary:read", "results:read"}),
        result_ids=frozenset({"res_001"}),
        upstream_capability="cap_001",
    )
    app_with_store.state.issuer.records["exchange_code_nr"] = record
    client = TestClient(
        app_with_store,
        base_url=AUDIENCE,
        client=("127.0.0.1", 1234),
        headers={"Origin": ORIGIN, "Host": "127.0.0.1:8012"},
    )
    token = client.post(
        "/analytics/v1/grants/exchange",
        json={"exchange_code": "exchange_code_nr"},
    ).json()["access_token"]

    resp = client.get(
        "/analytics/v1/datasets/test_ds_001/preview?image_path=flat.png",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "DATASET_NOT_READY"

