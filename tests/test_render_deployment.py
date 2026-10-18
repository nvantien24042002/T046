import os
import tempfile
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from backend.integrated_api import create_integrated_app
from storage.config import UploadConfig


def test_render_deployment_static_and_api(monkeypatch):
    dist_dir = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    assert (dist_dir / "index.html").is_file(), "React dist/index.html must exist (run npm run build)"

    with tempfile.TemporaryDirectory() as tmpdir:
        storage_root = Path(tmpdir) / "storage"
        storage_root.mkdir()

        secret = "s" * 32
        owners = {"admin": "k" * 32}
        config = UploadConfig(
            root=storage_root,
            secret=secret,
            min_free_bytes=0,
        )

        # Simulate Render environment with CVAT_ENFORCE_LOCAL=0
        monkeypatch.setenv("CVAT_ENFORCE_LOCAL", "0")

        app = create_integrated_app(
            config,
            owners,
            react_dist=dist_dir,
            origins=("https://cvat-dashboard.onrender.com",),
        )

        with TestClient(app, base_url="https://cvat-dashboard.onrender.com") as client:
            # 1. Health check from remote
            resp = client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"
            assert data["ready_datasets"] is True

            # 2. Root static SPA serving
            resp = client.get("/")
            assert resp.status_code == 200
            assert "<!doctype html>" in resp.text.lower() or "<html" in resp.text.lower()
            assert resp.headers.get("cache-control") == "no-store"

            # 3. SPA route fallback
            resp = client.get("/scene-finder")
            assert resp.status_code == 200
            assert "<!doctype html>" in resp.text.lower() or "<html" in resp.text.lower()

            resp = client.get("/labels")
            assert resp.status_code == 200
            assert "<!doctype html>" in resp.text.lower() or "<html" in resp.text.lower()

            # 4. Static assets serving
            assets = list((dist_dir / "assets").glob("*.js"))
            assert len(assets) > 0, "At least one JS bundle in dist/assets"
            asset_name = assets[0].name
            resp = client.get(f"/assets/{asset_name}")
            assert resp.status_code == 200
            assert resp.headers.get("x-content-type-options") == "nosniff"

            # 5. Security: sensitive or path traversal paths blocked
            resp = client.get("/.git/config")
            assert resp.status_code == 404
            resp = client.get("/api/nonexistent")
            assert resp.status_code == 404

            # 6. Browser session creation with configured origin
            resp = client.post(
                "/api/browser/v1/session",
                headers={
                    "x-requested-with": "N2-05B",
                    "origin": "https://cvat-dashboard.onrender.com",
                },
            )
            assert resp.status_code == 200
            session_data = resp.json()
            assert session_data["state"] == "active"
            assert "n205b_session" in resp.cookies


def test_factory_create_integrated_from_env_with_render_port(monkeypatch):
    """Verify backend.integrated_api:create_integrated_from_env correctly loads all settings."""
    from backend.integrated_api import create_integrated_from_env

    with tempfile.TemporaryDirectory() as tmpdir:
        storage_root = Path(tmpdir) / "storage"
        storage_root.mkdir()
        dist_dir = Path(__file__).resolve().parent.parent / "frontend" / "dist"

        monkeypatch.setenv("CVAT_UPLOAD_DIR", str(storage_root))
        monkeypatch.setenv("CVAT_UPLOAD_SECRET", "s" * 32)
        monkeypatch.setenv("CVAT_UPLOAD_OWNER_KEYS_JSON", '{"admin": "' + "k" * 32 + '"}')
        monkeypatch.setenv("CVAT_ENFORCE_LOCAL", "0")
        monkeypatch.setenv("CVAT_MIN_FREE_BYTES", "0")
        monkeypatch.setenv("CVAT_REACT_DIST", str(dist_dir))
        monkeypatch.setenv("CVAT_ALLOWED_ORIGINS", "https://cvat-dashboard.onrender.com")
        monkeypatch.setenv("PORT", "10000")

        app = create_integrated_from_env()
        assert app is not None
        assert app.title == "CVAT Integrated Backend (Stage 6A)"

        with TestClient(app, base_url="https://cvat-dashboard.onrender.com") as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"


def test_render_origin_and_privilege_protection(monkeypatch):
    """Verify CVAT_ENFORCE_LOCAL=0 still blocks unauthorized origins and protects privileged APIs."""
    from backend.integrated_api import create_integrated_app

    with tempfile.TemporaryDirectory() as tmpdir:
        storage_root = Path(tmpdir) / "storage"
        storage_root.mkdir()
        config = UploadConfig(root=storage_root, secret="s" * 32, min_free_bytes=0)
        owners = {"admin": "k" * 32}

        monkeypatch.setenv("CVAT_ENFORCE_LOCAL", "0")

        app = create_integrated_app(
            config,
            owners,
            origins=("https://approved.onrender.com",),
        )

        with TestClient(app, base_url="https://approved.onrender.com") as client:
            # 1. Foreign/unapproved origin rejected
            resp = client.post(
                "/api/browser/v1/session",
                headers={
                    "x-requested-with": "N2-05B",
                    "origin": "https://malicious-attacker.com",
                },
            )
            assert resp.status_code == 403
            assert resp.json()["error"]["code"] == "ORIGIN_DENIED"

            # 2. Missing custom header rejected
            resp = client.post(
                "/api/browser/v1/session",
                headers={"origin": "https://approved.onrender.com"},
            )
            assert resp.status_code == 403
            assert resp.json()["error"]["code"] == "BROWSER_REQUEST_REQUIRED"

            # 3. Privileged domain API without owner key rejected
            resp = client.post(
                "/api/v1/uploads",
                headers={"Content-Type": "application/json"},
                json={"filename": "test.zip", "size_bytes": 1000, "chunk_size_bytes": 1000},
            )
            assert resp.status_code == 403
            assert resp.json()["error"]["code"] == "ACCESS_DENIED"

            # 4. Privileged domain API with wrong owner key rejected
            resp = client.post(
                "/api/v1/uploads",
                headers={
                    "Content-Type": "application/json",
                    "X-Owner-Key": "wrong_key_that_does_not_match",
                },
                json={"filename": "test.zip", "size_bytes": 1000, "chunk_size_bytes": 1000},
            )
            assert resp.status_code == 403
            assert resp.json()["error"]["code"] == "ACCESS_DENIED"


def test_one_active_upload_policy_and_cancellation_recovery(monkeypatch):
    """Verify ONE_ACTIVE_UPLOAD triggers on concurrent attempts and clears after cancellation."""
    from backend.integrated_api import create_integrated_app

    with tempfile.TemporaryDirectory() as tmpdir:
        storage_root = Path(tmpdir) / "storage"
        storage_root.mkdir()
        config = UploadConfig(root=storage_root, secret="s" * 32, min_free_bytes=0)
        owners = {"admin": "k" * 32}

        monkeypatch.setenv("CVAT_ENFORCE_LOCAL", "0")

        app = create_integrated_app(
            config,
            owners,
            origins=("https://approved.onrender.com",),
        )

        with TestClient(app, base_url="https://approved.onrender.com") as client:
            # Create session
            session_resp = client.post(
                "/api/browser/v1/session",
                headers={
                    "x-requested-with": "N2-05B",
                    "origin": "https://approved.onrender.com",
                },
            )
            assert session_resp.status_code == 200

            # Start first upload
            res1 = client.post(
                "/api/browser/v1/uploads",
                headers={
                    "x-requested-with": "N2-05B",
                    "origin": "https://approved.onrender.com",
                    "Idempotency-Key": "test_idempotency_key_001",
                },
                json={"filename": "test1.zip", "size_bytes": 1000},
            )
            assert res1.status_code == 201
            upload_id = res1.json()["upload_id"]

            # Second distinct upload triggers 429 ONE_ACTIVE_UPLOAD
            res2 = client.post(
                "/api/browser/v1/uploads",
                headers={
                    "x-requested-with": "N2-05B",
                    "origin": "https://approved.onrender.com",
                    "Idempotency-Key": "test_idempotency_key_002",
                },
                json={"filename": "test2.zip", "size_bytes": 2000},
            )
            assert res2.status_code == 429
            assert res2.json()["error"]["code"] == "ONE_ACTIVE_UPLOAD"

            # Cancel first upload
            cancel_res = client.delete(
                f"/api/browser/v1/uploads/{upload_id}",
                headers={
                    "x-requested-with": "N2-05B",
                    "origin": "https://approved.onrender.com",
                },
            )
            assert cancel_res.status_code == 200

            # Now a new upload succeeds
            res3 = client.post(
                "/api/browser/v1/uploads",
                headers={
                    "x-requested-with": "N2-05B",
                    "origin": "https://approved.onrender.com",
                    "Idempotency-Key": "test_idempotency_key_002",
                },
                json={"filename": "test2.zip", "size_bytes": 2000},
            )
            assert res3.status_code == 201


