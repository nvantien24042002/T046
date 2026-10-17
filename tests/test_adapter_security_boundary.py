"""Credential boundary checks using fabricated credentials and in-process IO only."""
import io

import httpx
import pytest

from ui.backend_adapter import BackendClient, BackendError


OPERATIONS = [
    lambda c: c.create_upload("test.zip", 1, "fake-idempotency"),
    lambda c: c.upload_chunk("fake-id", "fake-capability", 0, 0, b"a"),
    lambda c: c.complete_upload("fake-id", "fake-capability"),
    lambda c: c.get_upload_status("fake-id", "fake-capability"),
    lambda c: c.start_validation("fake-id", "fake-capability"),
    lambda c: c.get_dataset_status("fake-id", "fake-capability"),
    lambda c: c.cancel_validation("fake-id", "fake-capability"),
    lambda c: c.get_dataset_summary("fake-id", "fake-capability"),
    lambda c: c.trigger_analysis("fake-id", "fake-capability"),
    lambda c: c.get_analysis_results("fake-id", "fake-capability"),
    lambda c: c.fetch_preview("fake-id", "fake-capability", "image.jpg"),
]


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("target", [
    "http://outside.invalid/steal", "http://127.0.0.1:8012/steal", "/same-origin",
])
def test_every_credential_route_blocks_redirect(operation, status, target, caplog):
    seen = []
    def handle(request):
        seen.append(request)
        return httpx.Response(status, headers={"Location": target}, content=b"fake-sensitive-body")
    # Deliberately enable redirects on the injected client to exercise the override.
    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True,
                      headers={"Authorization": "Bearer fake-authorization"}) as client:
        adapter = BackendClient(owner_key="fake-owner", http_client=client)
        with pytest.raises(BackendError) as caught:
            operation(adapter)
        assert not client.is_closed
    assert caught.value.code == "BACKEND_REDIRECT_BLOCKED"
    assert caught.value.status_code == status
    assert len(seen) == 1
    assert seen[0].url.host == "127.0.0.1"
    assert seen[0].url.port == 8011
    assert seen[0].headers["X-Owner-Key"] == "fake-owner"
    assert seen[0].headers["Authorization"] == "Bearer fake-authorization"
    if operation is not OPERATIONS[0]:
        assert seen[0].headers["Upload-Capability"] == "fake-capability"
    assert all(r.url.host != "outside.invalid" and r.url.port != 8012 for r in seen)
    for sensitive in ("fake-owner", "fake-capability", "fake-authorization", "fake-sensitive-body", target):
        assert sensitive not in str(caught.value)
        assert sensitive not in caplog.text


def test_recovery_receipts_redirect_blocked():
    seen = []
    def handle(request):
        seen.append(request)
        if request.url.path.endswith("/receipts"):
            return httpx.Response(307, headers={"Location": "https://outside.invalid/steal"})
        return httpx.Response(200, json={
            "upload_id": "fake-id", "filename": "test.zip", "size_bytes": 1, "received_bytes": 1,
            "chunk_size_bytes": 1, "state": "uploading",
        })
    with httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
        adapter = BackendClient(owner_key="fake-owner", http_client=client)
        with pytest.raises(BackendError) as caught:
            adapter.stream_upload(io.BytesIO(b"a"), "test.zip", 1,
                                  recovery_session={"upload_id": "fake-id", "upload_capability": "fake-capability"},
                                  confirmed_legacy_source=True)
    assert caught.value.code == "BACKEND_REDIRECT_BLOCKED"
    assert len(seen) == 2
    assert all(r.url.host == "127.0.0.1" for r in seen)


@pytest.mark.parametrize("url", [
    "http://localhost:0", "http://localhost:65536", "http://localhost:bad",
    "http://outside.invalid", "ftp://localhost", "http://user:fake@localhost",
    "http://localhost/?fake=secret", "http://localhost/#fake", "http://localhost\t",
    "http://127.0.0.1.outside.invalid", "//localhost:8011",
])
def test_invalid_url_rejected_before_any_request(url):
    seen = []
    with httpx.Client(transport=httpx.MockTransport(lambda r: seen.append(r))) as client:
        with pytest.raises(BackendError) as caught:
            BackendClient(url, "fake-owner", http_client=client).create_upload("test.zip", 1)
    assert caught.value.code == "INVALID_BACKEND_URL"
    assert seen == []


@pytest.mark.parametrize("url", ["HTTP://LOCALHOST:8011/", "http://127.0.0.1:8011", "http://[::1]:8011"])
def test_owned_client_ignores_environment_proxy_at_transport_boundary(monkeypatch, url):
    from httpx import _client
    proxy_reads = []
    def environment_proxies():
        proxy_reads.append(True)
        return {"all://": "http://environment-proxy.invalid:3128"}
    monkeypatch.setattr(_client, "get_environment_proxies", environment_proxies)
    seen = []
    mock = httpx.MockTransport(lambda r: (seen.append(r), httpx.Response(201, json={"upload_id": "fake-id", "upload_capability": "fake-capability"}))[1])
    # Keep HTTPX's real client and transport selection. Replace only the final IO
    # method so even a routing regression cannot open a socket.
    transports = []
    original_init = httpx.HTTPTransport.__init__
    def initialize(transport, *args, **kwargs):
        transports.append(kwargs.get("proxy"))
        original_init(transport, *args, **kwargs)
    monkeypatch.setattr(httpx.HTTPTransport, "__init__", initialize)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", lambda transport, request: mock.handle_request(request))
    assert BackendClient(url, "fake-owner").create_upload("test.zip", 1)["upload_id"] == "fake-id"
    assert proxy_reads == []
    assert transports == [None]
    assert len(seen) == 1
    assert seen[0].headers["X-Owner-Key"] == "fake-owner"


def test_injected_network_proxy_client_rejected_before_io(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("network transport must never be invoked")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    with httpx.Client(proxy="http://explicit-proxy.invalid:3128", follow_redirects=True) as client:
        with pytest.raises(BackendError) as caught:
            BackendClient(owner_key="fake-owner", http_client=client).create_upload("test.zip", 1)
    assert caught.value.code == "UNSAFE_BACKEND_CLIENT"


def test_request_origin_checked_even_for_another_loopback_port():
    seen = []
    with httpx.Client(transport=httpx.MockTransport(lambda r: seen.append(r))) as client:
        adapter = BackendClient(owner_key="fake-owner", http_client=client)
        with adapter._client() as guarded:
            with pytest.raises(BackendError) as caught:
                guarded.get("http://127.0.0.1:8012/steal", headers=adapter._headers("fake-capability"))
    assert caught.value.code == "INVALID_BACKEND_URL"
    assert seen == []
