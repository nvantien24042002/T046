"""Isolated Phase 1 regressions; no live endpoints or private configuration."""
import asyncio
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.integrated_api import create_integrated_app
from datasets.manager import DatasetManager
from storage.config import UploadConfig
from storage.uploads import UploadStore
from ui.backend_adapter import BackendClient, BackendError


@pytest.mark.parametrize("url", [
    "http://example.invalid", "http://127.0.0.1.evil.invalid",
    "http://127.0.0.1@evil.invalid", "http://user:password@localhost",
    "http://localhost/?secret=fake", "http://localhost/#fragment",
    "ftp://localhost", "http://2130706433", "http://localhost\n",
])
def test_credential_destination_rejected(url, caplog):
    requests = []
    with httpx.Client(transport=httpx.MockTransport(lambda r: requests.append(r))) as client:
        with pytest.raises(BackendError, match="loopback"):
            BackendClient(url, owner_key="fake-owner", http_client=client)
    assert requests == []
    assert "password" not in caplog.text
    assert "fake-owner" not in caplog.text


@pytest.mark.parametrize("url,host", [
    ("HTTP://LOCALHOST:8011/", "localhost"),
    ("http://127.0.0.1:8011", "127.0.0.1"),
    ("http://[::1]:8011", "::1"),
])
def test_normalized_loopback_destination(url, host):
    def handle(request):
        assert request.url.host == host
        assert request.headers["X-Owner-Key"] == "fake-owner"
        return httpx.Response(201, json={"upload_id": "fake", "upload_capability": "fake-capability"})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        adapter = BackendClient(url, "fake-owner", http_client=client)
        assert adapter.create_upload("arbitrary.zip", 1)["upload_id"] == "fake"
        adapter.base_url = "http://evil.invalid"
        with pytest.raises(BackendError):
            adapter.create_upload("arbitrary.zip", 1)


def config(tmp_path):
    return UploadConfig(tmp_path / "isolated", "s" * 40, min_free_bytes=0)


@pytest.mark.parametrize("stage", ["manager", "manager_after_open", "cache", "task"])
def test_startup_failure_releases_store_and_lock(tmp_path, monkeypatch, stage):
    cfg = config(tmp_path)
    opened = []
    def store_factory(cfg):
        store = UploadStore(cfg)
        opened.append(store)
        return store
    def manager_factory(store, limits):
        if stage == "manager":
            raise RuntimeError("injected startup failure")
        if stage == "manager_after_open":
            DatasetManager(store, limits)
            raise RuntimeError("injected failure after manager opened")
        if stage == "cache":
            (store.root / ".analysis-cache-v1").write_text("isolated obstruction")
        return object()
    app = create_integrated_app(cfg, {"alice": "a" * 40},
                                store_factory=store_factory, manager_factory=manager_factory)
    async def exercise():
        with monkeypatch.context() as patch:
            if stage == "task":
                def fail_task(worker):
                    raise RuntimeError("injected task failure")
                patch.setattr(asyncio, "create_task", fail_task)
            with pytest.raises((RuntimeError, FileExistsError)):
                async with app.router.lifespan_context(app):
                    pytest.fail("startup should fail")
        assert len(opened) == 1
        assert opened[0].db is None
        assert opened[0].process_lock is None
        if stage == "cache":
            (cfg.root / ".analysis-cache-v1").unlink()
        replacement = create_integrated_app(cfg, {"alice": "a" * 40})
        async with replacement.router.lifespan_context(replacement):
            assert replacement.state.store.db.execute("SELECT 1").fetchone()[0] == 1
    asyncio.run(exercise())


def test_partial_store_failure_releases_resources(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    opened = []
    def fail_recovery(store):
        opened.append(store)
        raise RuntimeError("injected recovery failure")
    async def exercise():
        with monkeypatch.context() as patch:
            patch.setattr(UploadStore, "recover", fail_recovery)
            app = create_integrated_app(cfg, {"alice": "a" * 40})
            with pytest.raises(RuntimeError, match="recovery"):
                async with app.router.lifespan_context(app):
                    pytest.fail("startup should fail")
        assert opened[0].db is None
        assert opened[0].process_lock is None
        replacement = create_integrated_app(cfg, {"alice": "a" * 40})
        async with replacement.router.lifespan_context(replacement):
            assert replacement.state.unhealthy is False
    asyncio.run(exercise())


@pytest.mark.parametrize("message,known", [
    ("DATASET_PAYLOAD_PINNED", True),
    ("VALIDATION_WORKSPACE_RESERVED", True),
    ("UNRECOGNIZED_POLICY", False),
])
def test_actual_trigger_classification(tmp_path, message, known):
    # Triggers exist only in an unrelated in-memory test database.
    db = sqlite3.connect(":memory:")
    try:
        db.executescript("CREATE TABLE probe (id INTEGER); CREATE TRIGGER reject_probe "
                         "BEFORE INSERT ON probe BEGIN SELECT RAISE(ABORT,'" + message + "'); END;")
        try:
            db.execute("INSERT INTO probe VALUES (1)")
        except sqlite3.IntegrityError as error:
            failure = error
    finally:
        db.close()
    assert failure.sqlite_errorcode == sqlite3.SQLITE_CONSTRAINT_TRIGGER
    check_error_response(tmp_path, failure, 409 if known else 503, message if known else "REGISTRY_UNAVAILABLE")


@pytest.mark.parametrize("error", [
    sqlite3.IntegrityError("DATASET_PAYLOAD_PINNED"),
    sqlite3.IntegrityError("UNIQUE constraint failed: uploads.id"),
    sqlite3.OperationalError("database is locked"),
    sqlite3.DatabaseError("database disk image is malformed"),
])
def test_unknown_database_errors_remain_unhealthy(tmp_path, error):
    check_error_response(tmp_path, error, 503, "REGISTRY_UNAVAILABLE")


def check_error_response(tmp_path, failure, status, code):
    app = create_integrated_app(config(tmp_path), {"alice": "a" * 40})
    @app.get("/isolated-error")
    def fail():
        raise failure
    with TestClient(app, base_url="http://127.0.0.1:8011", client=("127.0.0.1", 12345)) as client:
        response = client.get("/isolated-error")
        assert response.status_code == status
        assert response.json() == {"error": {"code": code}}
        assert app.state.unhealthy is (status == 503)
        assert client.get("/health").status_code == (503 if status == 503 else 200)


def test_environment_proxy_can_route_loopback_without_network(monkeypatch):
    # Inspect HTTPX routing with fabricated proxies, never connect or print host configuration.
    from httpx import _client
    monkeypatch.setattr(_client, "get_environment_proxies", lambda: {"http://": "http://proxy.invalid:8080"})
    with httpx.Client() as client:
        assert client._transport_for_url(httpx.URL("http://127.0.0.1:8011")) is not client._transport
    monkeypatch.setattr(_client, "get_environment_proxies", lambda: {
        "http://": "http://proxy.invalid:8080", "all://127.0.0.1": None,
    })
    with httpx.Client() as client:
        assert client._transport_for_url(httpx.URL("http://127.0.0.1:8011")) is client._transport
