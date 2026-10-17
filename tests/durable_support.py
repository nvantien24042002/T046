"""Local test server lifecycle; never attaches to or terminates another server."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time

import httpx
import psutil

from tests.fixtures import ROOT


class DurableServer:
    def __init__(self, folder: Path):
        self.folder, self.root = folder, folder / "private"
        self.key, self.secret = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        self.url = "http://127.0.0.1:8011"
        self.client = httpx.Client(base_url=self.url, timeout=15)
        self.process = None
        self.log = None

    def start(self):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", 8011)) == 0:
                raise RuntimeError("Port 8011 already occupied; preserving existing service.")
        environment = {**os.environ, "CVAT_UPLOAD_DIR": str(self.root), "CVAT_UPLOAD_SECRET": self.secret,
            "CVAT_UPLOAD_OWNER_KEYS_JSON": json.dumps({"alice": self.key}), "CVAT_CHUNK_BYTES": "65536",
            "CVAT_MAX_UPLOAD_BYTES": str(2 * 1024**2), "CVAT_STORAGE_QUOTA_BYTES": str(8 * 1024**2),
            "CVAT_MIN_FREE_BYTES": "0"}
        self.log = (self.folder / "durable-api.log").open("a", encoding="utf-8")
        self.process = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.durable_api:create_from_env",
            "--factory", "--host", "127.0.0.1", "--port", "8011", "--no-proxy-headers"],
            cwd=ROOT, env=environment, stdout=self.log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("Test child API exited; inspect its private test log.")
            try:
                if self.client.get("/health").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise RuntimeError("Durable test child startup timed out.")

    def stop(self, *, kill=False):
        if self.process is not None and self.process.poll() is None:
            # Windows venv python.exe can be a redirector with an actual Python
            # child. Terminate only descendants of this test-owned Popen process,
            # so killing the launcher cannot leave the real API alive.
            descendants = psutil.Process(self.process.pid).children(recursive=True)
            for child in reversed(descendants):
                try:
                    child.kill() if kill else child.terminate()
                except psutil.NoSuchProcess:
                    pass
            self.process.kill() if kill else self.process.terminate()
            self.process.wait(timeout=15)
            _, alive = psutil.wait_procs(descendants, timeout=10)
            for child in alive:
                child.kill()
            psutil.wait_procs(alive, timeout=5)
        if self.log is not None:
            self.log.close()

    def restart(self):
        self.stop(kill=True)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", 8011)) != 0:
                    break
            time.sleep(0.05)
        self.start()

    def headers(self, upload_id: str) -> dict[str, str]:
        capability = hmac.new(self.secret.encode(), f"alice:{upload_id}".encode(), hashlib.sha256).hexdigest()
        return {"X-Owner-Key": self.key, "Upload-Capability": capability}

    def close(self):
        self.stop()
        self.client.close()
