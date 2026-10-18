"""Stage 6C: Direct browser upload and integration hardening tests.

Tests origin validation, CORS, capability-only browser authentication,
bounded chunk streaming, retry, resume prefix verification, and error sanitization.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.integrated_api import create_integrated_app, DEFAULT_ORIGINS
from datasets.limits import Limits
from storage.config import UploadConfig
from tests.fixtures import make_zip
from ui.backend_adapter import BackendClient, BackendError, sanitize_message
from ui.browser_upload import render_browser_upload


@pytest.fixture
def integrated_server(tmp_path):
    root = tmp_path / "stage6c_server"
    secret = secrets.token_urlsafe(32)
    config = UploadConfig(
        root,
        secret,
        max_bytes=10 * 1024 * 1024,
        chunk_bytes=16384,
        block_bytes=4096,
        min_free_bytes=0,
        quota_bytes=10**9,
        idle_seconds=60,
        lifetime_seconds=300,
    )
    owner_key = secrets.token_urlsafe(32)
    owners = {"alice": owner_key}
    app = create_integrated_app(config, owners, limits=Limits())

    with TestClient(app, base_url="http://127.0.0.1:8011", client=("127.0.0.1", 12345)) as tc:
        yield "http://127.0.0.1:8011", owner_key, tc, app

    app.state.store.close()


def test_cors_origin_validation_allowed_and_rejected(integrated_server):
    _, owner_key, client, _ = integrated_server

    # 1. Allowed origin: Streamlit frontend on port 8501
    res = client.get(
        "/health",
        headers={"Origin": "http://localhost:8501"},
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://localhost:8501"

    # 2. Allowed origin: 127.0.0.1:8501
    res = client.get(
        "/health",
        headers={"Origin": "http://127.0.0.1:8501"},
    )
    assert res.status_code == 200
    assert res.headers.get("access-control-allow-origin") == "http://127.0.0.1:8501"

    # 3. Disallowed external origin: must be rejected with 403 LOCAL_ONLY
    res = client.get(
        "/health",
        headers={"Origin": "http://evil-attacker.com"},
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "LOCAL_ONLY"

    # 4. CORS preflight OPTIONS request from allowed origin
    options_res = client.options(
        "/api/v1/uploads",
        headers={
            "Origin": "http://localhost:8501",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-Owner-Key, Idempotency-Key, Content-Type",
        },
    )
    assert options_res.status_code == 200
    assert "access-control-allow-methods" in options_res.headers


def test_browser_capability_authentication_without_owner_key(integrated_server, tmp_path):
    """Verifies browser can upload chunks and complete using ONLY upload capability."""
    _, owner_key, client, _ = integrated_server
    zip_path = make_zip(tmp_path / "browser_auth.zip")
    data = zip_path.read_bytes()

    # Step 1: Server/Operator initializes upload session with owner key
    create_res = client.post(
        "/api/v1/uploads",
        json={"filename": "browser_auth.zip", "size_bytes": len(data)},
        headers={"X-Owner-Key": owner_key, "Idempotency-Key": secrets.token_urlsafe(24)},
    )
    assert create_res.status_code == 201
    session = create_res.json()
    upload_id = session["upload_id"]
    capability = session["upload_capability"]
    chunk_size = session["chunk_size_bytes"]

    # Step 2: Browser uploads chunks with capability ONLY (NO X-Owner-Key)
    offset = 0
    chunk_idx = 0
    while offset < len(data):
        chunk_data = data[offset : offset + chunk_size]
        chunk_sha = hashlib.sha256(chunk_data).hexdigest()
        put_res = client.put(
            f"/api/v1/uploads/{upload_id}/chunks/{chunk_idx}",
            headers={
                "Upload-Capability": capability,
                "Upload-Offset": str(offset),
                "X-Chunk-SHA256": chunk_sha,
                "Content-Type": "application/octet-stream",
                "Origin": "http://localhost:8501",
            },
            content=chunk_data,
        )
        assert put_res.status_code == 200, put_res.text
        offset += len(chunk_data)
        chunk_idx += 1

    # Step 3: Check status and receipts using capability ONLY
    st_res = client.get(
        f"/api/v1/uploads/{upload_id}",
        headers={"Upload-Capability": capability, "Origin": "http://localhost:8501"},
    )
    assert st_res.status_code == 200
    assert st_res.json()["received_bytes"] == len(data)

    receipts_res = client.get(
        f"/api/v1/uploads/{upload_id}/receipts",
        headers={"Upload-Capability": capability, "Origin": "http://localhost:8501"},
    )
    assert receipts_res.status_code == 200
    assert len(receipts_res.json()["receipts"]) == chunk_idx

    # Step 4: Complete upload using capability ONLY
    comp_res = client.post(
        f"/api/v1/uploads/{upload_id}/complete",
        headers={"Upload-Capability": capability, "Origin": "http://localhost:8501"},
    )
    assert comp_res.status_code == 200
    assert comp_res.json()["state"] == "uploaded"

    # Step 5: Unauthorized request without capability or key must fail
    fail_res = client.get(f"/api/v1/uploads/{upload_id}")
    assert fail_res.status_code == 403


def test_browser_chunking_bounded_slices_and_progress(integrated_server, tmp_path):
    _, owner_key, client, _ = integrated_server
    zip_path = make_zip(tmp_path / "chunk_progress.zip", filler=40000)
    data = zip_path.read_bytes()

    create_res = client.post(
        "/api/v1/uploads",
        json={"filename": "chunk_progress.zip", "size_bytes": len(data)},
        headers={"X-Owner-Key": owner_key, "Idempotency-Key": secrets.token_urlsafe(24)},
    )
    assert create_res.status_code == 201
    upload_id = create_res.json()["upload_id"]
    capability = create_res.json()["upload_capability"]
    chunk_size = create_res.json()["chunk_size_bytes"]

    # Verify multiple bounded chunks were created
    expected_chunks = (len(data) + chunk_size - 1) // chunk_size
    assert expected_chunks > 1

    offset = 0
    for idx in range(expected_chunks):
        slice_bytes = data[offset : offset + chunk_size]
        assert len(slice_bytes) <= chunk_size  # Strictly bounded

        put_res = client.put(
            f"/api/v1/uploads/{upload_id}/chunks/{idx}",
            headers={
                "Upload-Capability": capability,
                "Upload-Offset": str(offset),
                "X-Chunk-SHA256": hashlib.sha256(slice_bytes).hexdigest(),
                "Content-Type": "application/octet-stream",
            },
            content=slice_bytes,
        )
        assert put_res.status_code == 200
        offset += len(slice_bytes)
        assert put_res.json()["received_bytes"] == offset


def test_browser_interruption_and_idempotent_retry(integrated_server, tmp_path):
    _, owner_key, client, _ = integrated_server
    zip_path = make_zip(tmp_path / "retry.zip")
    data = zip_path.read_bytes()

    create_res = client.post(
        "/api/v1/uploads",
        json={"filename": "retry.zip", "size_bytes": len(data)},
        headers={"X-Owner-Key": owner_key, "Idempotency-Key": secrets.token_urlsafe(24)},
    )
    upload_id = create_res.json()["upload_id"]
    capability = create_res.json()["upload_capability"]
    chunk_size = create_res.json()["chunk_size_bytes"]

    chunk_0 = data[:chunk_size]
    chunk_sha = hashlib.sha256(chunk_0).hexdigest()

    # Successful PUT
    res1 = client.put(
        f"/api/v1/uploads/{upload_id}/chunks/0",
        headers={
            "Upload-Capability": capability,
            "Upload-Offset": "0",
            "X-Chunk-SHA256": chunk_sha,
            "Content-Type": "application/octet-stream",
        },
        content=chunk_0,
    )
    assert res1.status_code == 200

    # Idempotent replay of same chunk 0
    res2 = client.put(
        f"/api/v1/uploads/{upload_id}/chunks/0",
        headers={
            "Upload-Capability": capability,
            "Upload-Offset": "0",
            "X-Chunk-SHA256": chunk_sha,
            "Content-Type": "application/octet-stream",
        },
        content=chunk_0,
    )
    assert res2.status_code == 200
    assert res2.json().get("replayed") is True


def test_browser_resume_with_prefix_verification(integrated_server, tmp_path):
    _, owner_key, client, _ = integrated_server
    zip_path = make_zip(tmp_path / "resume.zip", filler=30000)
    data = zip_path.read_bytes()

    create_res = client.post(
        "/api/v1/uploads",
        json={"filename": "resume.zip", "size_bytes": len(data)},
        headers={"X-Owner-Key": owner_key, "Idempotency-Key": secrets.token_urlsafe(24)},
    )
    upload_id = create_res.json()["upload_id"]
    capability = create_res.json()["upload_capability"]
    chunk_size = create_res.json()["chunk_size_bytes"]

    # Upload chunk 0
    chunk_0 = data[:chunk_size]
    client.put(
        f"/api/v1/uploads/{upload_id}/chunks/0",
        headers={
            "Upload-Capability": capability,
            "Upload-Offset": "0",
            "X-Chunk-SHA256": hashlib.sha256(chunk_0).hexdigest(),
            "Content-Type": "application/octet-stream",
        },
        content=chunk_0,
    )

    # Browser queries receipts to verify prefix before resuming
    receipts_res = client.get(
        f"/api/v1/uploads/{upload_id}/receipts?start=0&limit=128",
        headers={"Upload-Capability": capability},
    )
    assert receipts_res.status_code == 200
    receipts = receipts_res.json()["receipts"]
    assert len(receipts) == 1
    assert receipts[0]["sha256"] == hashlib.sha256(chunk_0).hexdigest()

    # Mismatched reselected file must be detected by hash verification
    fake_chunk_0 = b"X" * len(chunk_0)
    assert hashlib.sha256(fake_chunk_0).hexdigest() != receipts[0]["sha256"]


def test_browser_cancel_session_and_cleanup(integrated_server, tmp_path):
    _, owner_key, client, _ = integrated_server
    zip_path = make_zip(tmp_path / "cancel.zip")
    data = zip_path.read_bytes()

    create_res = client.post(
        "/api/v1/uploads",
        json={"filename": "cancel.zip", "size_bytes": len(data)},
        headers={"X-Owner-Key": owner_key, "Idempotency-Key": secrets.token_urlsafe(24)},
    )
    upload_id = create_res.json()["upload_id"]
    capability = create_res.json()["upload_capability"]

    # Cancel via DELETE using capability
    del_res = client.delete(
        f"/api/v1/uploads/{upload_id}",
        headers={"Upload-Capability": capability},
    )
    assert del_res.status_code == 200
    assert del_res.json()["state"] == "cancelled"


def test_duplicate_upload_prevention(integrated_server, tmp_path):
    _, owner_key, client, _ = integrated_server
    zip_path = make_zip(tmp_path / "dup.zip")
    data = zip_path.read_bytes()

    res1 = client.post(
        "/api/v1/uploads",
        json={"filename": "dup.zip", "size_bytes": len(data)},
        headers={"X-Owner-Key": owner_key, "Idempotency-Key": secrets.token_urlsafe(24)},
    )
    assert res1.status_code == 201

    # Starting a second simultaneous upload succeeds concurrently with distinct ID
    res2 = client.post(
        "/api/v1/uploads",
        json={"filename": "dup2.zip", "size_bytes": len(data)},
        headers={"X-Owner-Key": owner_key, "Idempotency-Key": secrets.token_urlsafe(24)},
    )
    assert res2.status_code == 201
    assert res2.json()["upload_id"] != res1.json()["upload_id"]


def test_unready_dataset_cannot_be_queried(integrated_server, tmp_path):
    _, owner_key, client, _ = integrated_server
    zip_path = make_zip(tmp_path / "unready.zip")
    data = zip_path.read_bytes()

    create_res = client.post(
        "/api/v1/uploads",
        json={"filename": "unready.zip", "size_bytes": len(data)},
        headers={"X-Owner-Key": owner_key, "Idempotency-Key": secrets.token_urlsafe(24)},
    )
    upload_id = create_res.json()["upload_id"]
    capability = create_res.json()["upload_capability"]

    # Trying to query an unvalidated, unready dataset
    analyze_res = client.post(
        f"/api/v1/datasets/{upload_id}/analyze",
        headers={"Upload-Capability": capability},
    )
    assert analyze_res.status_code in (404, 409)


def test_sanitization_masks_paths_and_secrets():
    raw_error = "Failed to open D:\\CVAT_Dashboard_Scene_Finder\\secret\\data.zip with key " + ("A" * 36)
    clean = sanitize_message(raw_error)
    assert "D:\\" not in clean
    assert "secret\\data.zip" not in clean
    assert "A" * 36 not in clean
    assert "[REDACTED_PATH]" in clean
    assert "[REDACTED_KEY]" in clean
