from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
import pytest

from tests.durable_support import DurableServer
from tests.fixtures import ROOT, make_zip

pytestmark = pytest.mark.skipif(os.environ.get("CVAT_RUN_BROWSER_TESTS") != "1",
                               reason="Set CVAT_RUN_BROWSER_TESTS=1 for real Chrome durable protocol tests.")


@pytest.fixture
def browser_server(tmp_path):
    server = DurableServer(tmp_path)
    try:
        server.start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page()
            page.set_default_timeout(30000)
            yield page, server
            browser.close()
    finally:
        server.close()


def test_refresh_restart_reselection_prefix_and_cancel(browser_server, tmp_path):
    page, server = browser_server
    source = make_zip(tmp_path / "source.zip", filler=5 * 65536)
    successful = []
    def record(response):
        if response.request.method == "PUT" and response.status == 200:
            successful.append(int(response.url.rsplit("/", 1)[1]))
    page.on("response", record)
    page.goto(server.url + "/client")
    page.wait_for_load_state("networkidle")
    page.locator("#owner-key").fill(server.key)
    page.locator("#source").set_input_files(str(source))
    page.route("**/chunks/1", lambda route: route.abort("internetdisconnected"))
    page.locator("#upload").click()
    expect(page.locator("#status")).to_contain_text("NETWORK_INTERRUPTED", timeout=30000)
    upload_id = page.evaluate("JSON.parse(sessionStorage.getItem('cvat-stage3-session')).id")
    before = server.client.get("/api/v1/uploads/" + upload_id, headers=server.headers(upload_id)).json()
    assert before["received_bytes"] == 65536
    server.restart()
    page.unroute("**/chunks/1")
    page.reload()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#status")).to_contain_text("Saved session found")
    # Same filename and size, but changed prefix: must refuse before any new PUT.
    wrong_folder = tmp_path / "wrong"
    wrong_folder.mkdir()
    wrong = wrong_folder / "source.zip"
    with source.open("rb") as reader, wrong.open("wb") as writer:
        writer.write(b"X")
        reader.seek(1)
        while block := reader.read(65536):
            writer.write(block)
    page.locator("#source").set_input_files(str(wrong))
    page.locator("#upload").click()
    expect(page.locator("#status")).to_contain_text("SOURCE_PREFIX_MISMATCH")
    assert successful == [0]
    page.locator("#source").set_input_files(str(source))
    page.locator("#upload").click()
    expect(page.locator("#result")).to_contain_text('"state": "uploaded"')
    expect(page.locator("#result")).to_contain_text('"ready": false')
    chunks = (source.stat().st_size + 65535) // 65536
    assert successful == list(range(chunks)), successful
    payload = server.root / "uploads" / upload_id / "payload.part"
    with source.open("rb") as file:
        original_hash = hashlib.file_digest(file, "sha256").hexdigest()
    with payload.open("rb") as file:
        assert hashlib.file_digest(file, "sha256").hexdigest() == original_hash
    evidence = dict(browser=page.context.browser.version, bytes=source.stat().st_size, chunks=chunks,
        sha256=original_hash, acknowledged_before_restart=65536,
        refresh_and_api_kill_restart=True, different_prefix_rejected=True,
        acknowledged_prefix_retransmitted=False, state="uploaded", ready=False, validated=False)
    output = ROOT / "test-results"
    output.mkdir(exist_ok=True)
    (output / "stage3-browser-evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    page.screenshot(path=str(tmp_path / "durable-client.png"), full_page=True)
    page.locator("#cancel").click()
    expect(page.locator("#result")).to_contain_text('"state": "cancelled"')
    expect(page.locator("#result")).to_contain_text('"cleanup_pending": false')
    assert not payload.exists()


def test_browser_lost_ack_replays_same_chunk(browser_server, tmp_path):
    page, server = browser_server
    source = make_zip(tmp_path / "lost-ack.zip", filler=2 * 65536)
    lost = [False]
    replays = []
    def lose_once(route):
        if not lost[0]:
            lost[0] = True
            assert route.fetch().status == 200  # Server committed; browser never sees acknowledgement.
            route.abort("connectionreset")
        else:
            route.continue_()
    def response_seen(response):
        if response.request.method == "PUT" and response.status == 200:
            replays.append(response.json()["replayed"])
    page.on("response", response_seen)
    page.goto(server.url + "/client")
    page.wait_for_load_state("networkidle")
    page.route("**/chunks/0", lose_once)
    page.locator("#owner-key").fill(server.key)
    page.locator("#source").set_input_files(str(source))
    page.locator("#upload").click()
    expect(page.locator("#result")).to_contain_text('"state": "uploaded"')
    assert lost[0] and True in replays
    upload_id = page.evaluate("JSON.parse(sessionStorage.getItem('cvat-stage3-session')).id")
    result = server.client.get("/api/v1/uploads/" + upload_id, headers=server.headers(upload_id)).json()
    assert result["received_bytes"] == source.stat().st_size
    with (server.root / "uploads" / upload_id / "payload.part").open("rb") as file, source.open("rb") as original:
        assert hashlib.file_digest(file, "sha256").hexdigest() == hashlib.file_digest(original, "sha256").hexdigest()
