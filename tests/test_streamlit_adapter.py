"""Integration tests for the Streamlit backend adapter and frontend integration (Stage 6B)."""
import hashlib
import io
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from streamlit.testing.v1 import AppTest

from backend.integrated_api import create_integrated_app
from datasets.limits import Limits
from storage.config import UploadConfig
from tests.fixtures import ROOT, make_zip
from ui.backend_adapter import (
    BackendClient,
    BackendError,
    sanitize_message,
    DEFAULT_BACKEND_URL,
)


import httpx


@pytest.fixture
def backend_server(tmp_path):
    """Runs an in-process Stage 6A backend server using ASGITransport."""
    root = tmp_path / "private_adapter"
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

    from fastapi.testclient import TestClient
    with TestClient(app, base_url="http://127.0.0.1:8011", client=("127.0.0.1", 12345)) as tc:
        client = BackendClient("http://127.0.0.1:8011", owner_key, http_client=tc)
        yield "http://127.0.0.1:8011", owner_key, client

    app.state.store.close()



def test_adapter_health_check_available_and_unavailable(backend_server):
    base_url, owner_key, client = backend_server
    assert client.check_health() is True

    # Bad port should return False without raising uncaught exceptions
    dead_client = BackendClient("http://127.0.0.1:59999", owner_key)
    assert dead_client.check_health() is False


def test_adapter_upload_chunked_streaming_progress(backend_server, tmp_path):
    base_url, owner_key, client = backend_server
    zip_path = make_zip(tmp_path / "stream_fixture.zip")
    data = zip_path.read_bytes()
    stream = io.BytesIO(data)

    progress_events = []
    def on_progress(offset, total):
        progress_events.append((offset, total))

    session = client.stream_upload(stream, "stream_fixture.zip", len(data), on_progress=on_progress, chunk_bytes=4096)
    assert session["state"] == "uploaded"
    assert session["ready"] is False
    assert len(progress_events) > 0
    assert progress_events[-1] == (len(data), len(data))


def test_adapter_interrupted_upload_error_handling(backend_server):
    base_url, owner_key, client = backend_server
    # Invalid upload ID should raise clean BackendError
    with pytest.raises(BackendError) as exc_info:
        client.upload_chunk("nonexistent-upload-id", "fake-cap", 0, 0, b"data")
    assert exc_info.value.status_code in (403, 404)
    # Ensure no local file paths leaked
    assert "D:\\" not in exc_info.value.message and "C:\\" not in exc_info.value.message


def test_adapter_validation_lifecycle_polling_and_cancellation(backend_server, tmp_path):
    base_url, owner_key, client = backend_server
    zip_path = make_zip(tmp_path / "valid_fixture.zip")
    data = zip_path.read_bytes()

    session = client.stream_upload(io.BytesIO(data), "valid.zip", len(data), chunk_bytes=8192)
    upload_id = session["upload_id"]
    capability = session["upload_capability"]

    # Status before validation is not_requested
    st0 = client.get_dataset_status(upload_id, capability)
    assert st0["state"] == "not_requested"
    assert st0["ready"] is False

    # Start validation
    val_resp = client.start_validation(upload_id, capability)
    assert val_resp["state"] in ("validating", "indexing", "publishing", "ready")

    # Poll until ready
    deadline = time.monotonic() + 15
    while True:
        status = client.get_dataset_status(upload_id, capability)
        if status["state"] == "ready":
            break
        assert status["state"] in ("validating", "indexing", "publishing")
        if time.monotonic() > deadline:
            pytest.fail("Validation polling timed out")
        time.sleep(0.2)

    dataset_id = status["dataset_id"]
    assert status["ready"] is True

    # Dataset summary query
    summary = client.get_dataset_summary(dataset_id, capability)
    assert summary["state"] == "ready"
    assert summary["ready"] is True
    assert summary["images"] == 2
    assert summary["annotations"] is True


def test_adapter_analysis_trigger_and_caching(backend_server, tmp_path):
    base_url, owner_key, client = backend_server
    zip_path = make_zip(tmp_path / "analysis_fixture.zip")
    data = zip_path.read_bytes()

    session = client.stream_upload(io.BytesIO(data), "analysis.zip", len(data), chunk_bytes=8192)
    upload_id = session["upload_id"]
    capability = session["upload_capability"]

    client.start_validation(upload_id, capability)
    deadline = time.monotonic() + 15
    while True:
        status = client.get_dataset_status(upload_id, capability)
        if status["state"] == "ready":
            dataset_id = status["dataset_id"]
            break
        time.sleep(0.2)

    # 1. Before analysis: results endpoint returns None (handled 409 ANALYSIS_NOT_RUN)
    assert client.get_analysis_results(dataset_id, capability) is None

    # 2. Trigger analysis explicitly
    results = client.trigger_analysis(dataset_id, capability)
    assert "quality_rows" in results
    assert "audit_rows" in results
    assert len(results["quality_rows"]) == 2

    # 3. Subsequent call returns cached results identically without re-running
    cached = client.get_analysis_results(dataset_id, capability)
    assert cached == results


def test_adapter_fetch_preview_clean_and_annotated(backend_server, tmp_path):
    base_url, owner_key, client = backend_server
    zip_path = make_zip(tmp_path / "preview_fixture.zip")
    data = zip_path.read_bytes()

    session = client.stream_upload(io.BytesIO(data), "preview.zip", len(data), chunk_bytes=8192)
    upload_id = session["upload_id"]
    capability = session["upload_capability"]

    client.start_validation(upload_id, capability)
    deadline = time.monotonic() + 15
    while True:
        status = client.get_dataset_status(upload_id, capability)
        if status["state"] == "ready":
            dataset_id = status["dataset_id"]
            break
        time.sleep(0.2)

    # Fetch clean image preview
    raw_img = client.fetch_preview(dataset_id, capability, "images/flat.png", annotated=False)
    assert len(raw_img) > 0
    assert raw_img[:8] == b"\x89PNG\r\n\x1a\n"

    # Fetch annotated image preview
    annotated = client.fetch_preview(dataset_id, capability, "images/flat.png", annotated=True)
    assert len(annotated) > 0


def test_adapter_error_sanitization_no_paths_or_secrets():
    bad_msg = "Error opening file at D:\\CVAT_Dashboard\\secret\\token.key: permission denied"
    cleaned = sanitize_message(bad_msg)
    assert "D:\\" not in cleaned
    assert "[REDACTED_PATH]" in cleaned


def test_adapter_unready_dataset_access_forbidden(backend_server):
    base_url, owner_key, client = backend_server
    fake_id = "00000000-0000-0000-0000-000000000000"

    with pytest.raises(BackendError) as exc_info:
        client.get_dataset_summary(fake_id, "dummy-cap")
    assert exc_info.value.status_code == 404

    with pytest.raises(BackendError) as exc_info:
        client.trigger_analysis(fake_id, "dummy-cap")
    assert exc_info.value.status_code == 404


def test_existing_dashboard_behavior_preserved_offline():
    """Verify existing dashboard starts and runs without exceptions when backend is inactive."""
    page = AppTest.from_file(str(ROOT / "app.py")).run(timeout=30)
    assert not page.exception
    assert len(page.get("file_uploader")) == 1
