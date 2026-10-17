"""Opt-in real Chrome tests; starts only loopback child servers it owns."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import urllib.request

import pytest
from playwright.sync_api import sync_playwright, expect

from prototype.api import CHUNK_BYTES
from tests.fixtures import ROOT, make_zip

pytestmark = pytest.mark.skipif(os.environ.get("CVAT_RUN_BROWSER_TESTS") != "1",
                               reason="Set CVAT_RUN_BROWSER_TESTS=1 for local Chrome E2E.")


@pytest.fixture(scope="module")
def servers(tmp_path_factory):
    folder = tmp_path_factory.mktemp("cvat-browser")
    env = {**os.environ, "CVAT_PROTOTYPE_KEY": secrets.token_urlsafe(32),
           "CVAT_PROTOTYPE_DIR": str(folder / "private")}
    commands = [
        (8010, ["-m", "uvicorn", "prototype.api:create_from_env", "--factory", "--host", "127.0.0.1", "--port", "8010", "--no-proxy-headers"]),
        (8502, ["-m", "streamlit", "run", "prototype_app.py", "--server.address", "127.0.0.1", "--server.port", "8502", "--server.headless", "true", "--browser.gatherUsageStats", "false"]),
        (8501, ["-m", "streamlit", "run", "app.py", "--server.address", "127.0.0.1", "--server.port", "8501", "--server.headless", "true", "--browser.gatherUsageStats", "false"]),
    ]
    # Never attach to or terminate an existing user's server.
    for port, _ in commands:
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0, f"Port {port} already occupied"
    children = []
    try:
        for port, args in commands:
            log = (folder / f"server-{port}.log").open("w", encoding="utf-8")
            process = subprocess.Popen([sys.executable, *args], cwd=ROOT, env=env,
                stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            children.append((process, log))
            deadline = time.monotonic() + 45
            route = "/health" if port == 8010 else "/_stcore/health"
            while True:
                assert process.poll() is None, f"Server {port} exited; see {folder}"
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}{route}", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    pytest.fail(f"Server {port} startup timeout; see {folder}")
                time.sleep(0.2)
        yield folder
    finally:
        for process, log in reversed(children):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            log.close()


@pytest.fixture
def page(servers):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        page.set_default_timeout(30000)
        yield page
        browser.close()


def test_browser_chunks_rerun_and_file_hash(page, servers):
    fixture = make_zip(servers / "transport.zip", filler=8 * CHUNK_BYTES)
    requests = []
    acknowledgements = []
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append((request.method, request.url,
            int(request.headers.get("content-length", "0")))))
    def record_ack(response):
        if response.request.method == "PUT" and "/chunks/" in response.url and response.status == 200:
            acknowledgements.append(response.json()["received_bytes"])
    page.on("response", record_ack)
    page.goto("http://127.0.0.1:8502")
    page.wait_for_load_state("networkidle")
    # Chrome CDP throttles a real network upload so a Streamlit rerun occurs mid-file.
    cdp = page.context.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 10,
             "downloadThroughput": -1, "uploadThroughput": 128 * 1024})
    page.locator("#zip-file").set_input_files(str(fixture))
    page.get_by_role("button", name="Upload test ZIP", exact=True).click()
    expect(page.locator("#status")).to_contain_text("server received", timeout=30000)
    page.get_by_role("button", name="Rerun page", exact=True).click()
    expect(page.get_by_test_id("stJson")).to_contain_text("received_unvalidated", timeout=60000)
    expect(page.get_by_test_id("stJson")).to_contain_text('"ready":false')
    with fixture.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    expect(page.get_by_test_id("stJson")).to_contain_text(digest)
    chunks = [request for request in requests if request[0] == "PUT" and "/chunks/" in request[1]]
    assert len(chunks) == (fixture.stat().st_size + CHUNK_BYTES - 1) // CHUNK_BYTES
    # Chromium omits automatic Content-Length from Request.headers; actual API
    # acknowledgements count received body bytes and are the authoritative bound.
    assert len(acknowledgements) == len(chunks)
    assert all(0 < current - previous <= CHUNK_BYTES
               for previous, current in zip([0, *acknowledgements], acknowledgements))
    assert len([r for r in requests if r[0] == "POST" and r[1].endswith("/uploads")]) == 1
    assert not errors
    evidence = {"browser": page.context.browser.version,
                "transport_bytes": fixture.stat().st_size,
                "transport_chunks": len(chunks), "chunk_limit_bytes": CHUNK_BYTES,
                "sha256": digest, "rerun_during_upload": True,
                "state": "received_unvalidated", "ready": False, "durable": False}
    output = ROOT / "test-results"
    output.mkdir(exist_ok=True)
    (output / "browser-evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    page.screenshot(path=str(servers / "prototype.png"), full_page=True)


def test_existing_dashboard_tabs_preview_scene_and_csv(page, servers):
    fixture = make_zip(servers / "baseline.zip")
    page.goto("http://127.0.0.1:8501")
    page.wait_for_load_state("networkidle")
    page.locator('input[type="file"]').set_input_files(str(fixture))
    expect(page.get_by_role("tab")).to_have_count(7, timeout=60000)
    assert page.get_by_test_id("stException").count() == 0
    page.get_by_role("tab", name="Phân bố", exact=False).click()
    expect(page.get_by_text("Tìm ảnh theo thời tiết", exact=False)).to_be_visible()
    page.get_by_role("tab", name="Review ảnh", exact=False).click()
    expect(page.get_by_text("images/flat.png", exact=True)).to_be_visible()
    page.get_by_role("tab", name="Xuất báo cáo", exact=False).click()
    with page.expect_download() as download:
        page.get_by_role("button", name="Tải Image Quality CSV", exact=True).click()
    csv = download.value
    assert csv.suggested_filename == "image_quality_report.csv"
    csv_path = servers / csv.suggested_filename
    csv.save_as(str(csv_path))
    text = csv_path.read_text(encoding="utf-8-sig")
    assert "filename,status,score" in text and "images/flat.png,REVIEW,50" in text
    # The original download widget triggers a rerun; stale/new tabs coexist briefly.
    expect(page.get_by_role("tab")).to_have_count(7)
    page.get_by_role("tab", name="Coverage & Slice", exact=False).click()
    expect(page.get_by_text("Coverage theo class & slice", exact=False)).to_be_visible()
    assert page.get_by_test_id("stException").count() == 0
    page.screenshot(path=str(servers / "dashboard.png"), full_page=True)


def test_connection_failure_never_reports_ready(page, servers):
    fixture = make_zip(servers / "interrupted.zip", filler=CHUNK_BYTES)
    page.goto("http://127.0.0.1:8502")
    page.wait_for_load_state("networkidle")
    page.route("**/prototype/v1/uploads/*/chunks/*", lambda route: route.abort("internetdisconnected"))
    page.locator("#zip-file").set_input_files(str(fixture))
    page.get_by_role("button", name="Upload test ZIP", exact=True).click()
    expect(page.get_by_test_id("stJson")).to_contain_text('"state":"failed"')
    expect(page.get_by_test_id("stJson")).to_contain_text('"ready":false')
    expect(page.get_by_test_id("stJson")).to_contain_text("no retry/resume")
